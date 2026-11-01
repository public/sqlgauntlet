# Doom on SQLite's VDBE: port design

Goal: run the program behind Turso's "Running unmodified Doom in the SQLite bytecode language" on stock SQLite
3.54.1, through the real `sqlite3VdbeExec`, and then through cursed-sqlite-jit (qc, qvm). Exploration only.

## How Turso did it (blog + `tursodatabase/turso-vdbe-doom-example` @ 52be17f, MIT)

- **Compiler.** `doomgeneric` (C) plus a small libc/port shim → `clang -O2 -emit-llvm` → 82 `.ll` modules (checked in
  under `doom/ll/`) → **`vdbecc`** (Rust, ~4.6k lines: a `.ll` parser and an SSA → VDBE code generator) → a Turso
  `Program` built with Turso's own `ProgramBuilder`. No WASM.
- **ABI.** Each SSA value gets its own register, for the whole program, with no register allocator. Calls use `Gosub`/`Return`
  through fixed `LINK`/`ARG0..15`/`RET` registers. Recursive (call-graph cycle) functions spill live-across-call
  registers to a stack in linear memory. Function pointers are small integer IDs, dispatched through a balanced
  compare tree. Values are i64, kept sign-extended at their IR width with shift pairs. `xor` is `(a|b)-(a&b)`.
  Floats are REAL registers (`Real`, `RealAffinity`, `Cast`).
- **Memory.** The 64 MiB C address space is one BLOB: the `ram` column of the single row of `_vdbecc_mem`.
  Globals start at 0x1000 and the stack is at the top. The WAD is patched into a global. A cursor is opened on the row once
  (`OpenWrite` + `SeekRowid`). A load is `BlobRead(cursor, col, off, n)`, followed by one `get_byte(blob, i)` per byte and
  shift/or. A store is `BlobRead`, one `set_byte(blob, i, v)` per byte, then `BlobWrite`. `memcpy` is a bare `BlobRead` + `BlobWrite`.
  `get_byte`/`set_byte` are Turso's PostgreSQL-compatible scalar functions, called through the ordinary `Function` opcode.
- **Opcodes used (all of them):** Init, Transaction, Halt, Goto, Gosub, Return, Integer, Real, Blob, Copy, Add,
  AddImm, Subtract, Multiply, Divide, Remainder, BitAnd, BitOr, ShiftLeft, ShiftRight, Eq/Ne/Lt/Le/Gt/Ge, If,
  IsNull, Cast, RealAffinity, Function, Variable, ResultRow, OpenWrite, SeekRowid, BlobRead, BlobWrite, plus one
  Turso-only op, **`RegCopyOffset`** (indirect register store). `RegCopyOffset` is used once, to zero the function register file at startup.
- **Loading.** The fork adds a load path that wraps the built `Program` in an ordinary `Statement`. No SQL is involved.
- **Frames out.** `DG_DrawFrame` memcpys the screen into `vdbe_fb` (320×200×4 bytes) and calls the `vdbe_present`
  intrinsic: `BlobRead` of the framebuffer, then `ResultRow [0, fb]`. Each `step` returns one frame.
- **Input in.** `vdbe_fetch_input` reads `?1..?10` with `Variable`; the host rebinds them while the statement is paused.
  Unbound parameters read as 0, so no input means Doom's attract loop (title screen, then the recorded demos), which
  is deterministic. Time is virtual (`DG_GetTicksMs` adds 10 ms per call), so the output does not depend on wall time.

So nothing in the program needs a Turso-only *VM feature*. It needs two scalar functions, a blob-cursor pair and one
indirect register store. The port is practical.

## Port

```
turso-doom clone (gitignored) ─ docker rust:1.88, --cpuset-cpus 8-13 ─▶ vdbecc + our exporter bin (vdbe2sqlite.rs)
   ├─ prog.txt   Turso's built program, one resolved instruction per line (labels → pcs)
   ├─ ram.bin    the initial 64 MiB RAM image, WAD patched in (read back from _vdbecc_mem)
   └─ ref.txt    per-frame FNV-1a hashes from Turso's own engine (the reference)
doomsql.c  (#include build/gen/sqlite3.c from cursed-sqlite-jit, read-only; + qc.c for the JIT runs)
   reads prog.txt, translates each Turso instruction to SQLite ops (sqlite3VdbeAddOp*), with pc remapping, wraps it in a real
   Vdbe (sqlite3VdbeCreate / sqlite3VdbeMakeReady on a Parse), registers the memory functions, then steps it with
   sqlite3_step (stock) or sqlite3_qc_exec (JIT), hashing every ResultRow's frame.
```

Why a loader instead of SQL: arbitrary `Gosub` control flow, half a million registers and blob cursors can't be
expressed as SQL that SQLite would compile into equivalent bytecode. The loader builds a normal `Vdbe` with the
same calls the parser uses, so `sqlite3_step` runs it through `sqlite3VdbeExec` unchanged, and qc sees an ordinary
prepared statement. SQLite itself is not patched for this, beyond qc's own hooks. The loader sits in the same translation unit
as the amalgamation, as `sqlite3_blob_open` does internally.

Translation, Turso → SQLite (registers +1; operand order follows vdbe.c, e.g. `Subtract` is r[P3]=r[P2]-r[P1]):

| Turso | SQLite |
|---|---|
| Integer / Real / Blob / Copy / AddImm / Goto / Init | OP_Integer or OP_Int64 / OP_Real / OP_Blob / OP_Copy / OP_AddImm / OP_Goto / OP_Init |
| Add Subtract Multiply Divide Remainder BitAnd BitOr ShiftLeft ShiftRight | the same op, P1=rhs, P2=lhs, P3=dest |
| Eq…Ge (default flags) | the same op, P1=rhs, P3=lhs, P5=0 |
| If / IsNull / Cast / RealAffinity / Variable / ResultRow | the same op |
| Gosub / Return | OP_Gosub P1=link / OP_Return P1=link, P3=fallthrough (return addresses are created by SQLite's own Gosub, so they stay consistent) |
| Function get_byte / set_byte | OP_Function with user functions of the same semantics (PostgreSQL bytea) |
| BlobRead(cur, col, off, n) → d | 2×OP_SCopy into an arg window + OP_Function `vdbe_blob_read(off, n)` → d |
| BlobWrite(cur, col, off, src) → d | 2×OP_SCopy + OP_Function `vdbe_blob_write(off, src)` → d (an ack, as in Turso) |
| OpenWrite / SeekRowid on `_vdbecc_mem` / Transaction | OP_Noop (memory is a host buffer; see below) |
| RegCopyOffset (zero the function registers) | OP_Noop; the loader zeroes the range once at startup with log₂(n) doubling OP_Copy ops, reached from OP_Init |
| Halt | OP_Halt (errors: P1=SQLITE_ERROR, P4 message) |

**Memory.** The RAM is a 64 MiB host buffer that `vdbe_blob_read`/`vdbe_blob_write` use, not a table row. SQLite has no
`BlobRead`/`BlobWrite` opcodes (`sqlite3_blob_*` builds its own small program and calls B-tree functions directly), and putting the
row behind incremental-blob calls from inside a function would measure the pager, not the VDBE. The instruction stream is
otherwise Turso's, one for one: the same loads, byte codecs, shifts and calls. The statement then reads no table,
so it is read-only. That is also what qvm requires.

**JIT.** `sqlite3_qc(stmt)` first (the plan tiers decline, then qvm), and `QC_VM_ONLY=1` to force qvm. Things qvm may decline,
from reading `qc/qvm.c`; we ask the `sqlite-jit` session rather than editing:
- `v->nMem > 65536` → "qvm size". (Measured afterwards: Doom needs 40,563 registers, so this cap is not hit.)
- User functions must be built-in or `SQLITE_DETERMINISTIC`. The memory functions have side effects, so marking them
  deterministic is a lie that qvm may act on (CSE, hoisting); a JIT-side opt-in for side-effecting functions is the honest fix.
- `qv_exec` buffers every output row and calls the callback once at the end: frames would be held in memory (256 KB each).
- Runs must be inside a transaction (`BEGIN`), even with no table access.

**Correctness.** We take an FNV-1a-64 hash of each 256 000-byte frame. The references are:
1. Turso's own engine, running the same program;
2. stock SQLite, running the translated program.

Both qc and qvm must match (2) frame for frame. We also dump a few frames as PNG locally, never committed.

**Speed.** We time N frames of the attract loop (frames/s), on stock SQLite and qvm on the M5, and on the Xeon through the broker.
Fallback reasons are in `qc_why`/`qv_why`.

**Optional, later:** a "fused" export, where loads and stores become one `mem_load_N`/`mem_store_N` function call. Turso
says a similar specialisation reached 60 fps. That variant would measure qvm's control-flow and arithmetic speed, without the
blob-allocation cost of the byte codec.
