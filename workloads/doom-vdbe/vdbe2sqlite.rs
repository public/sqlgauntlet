//! vdbe2sqlite: compile Doom with vdbecc, then export what doomsql.c needs to run the same program on SQLite:
//!   OUT/prog.txt  every instruction of Turso's built program (labels resolved), one per line: `pc Op field=value ...`
//!   OUT/ram.bin   the initial RAM image (globals, WAD patched in), read back from `_vdbecc_mem`
//!   OUT/meta.txt  ram size, framebuffer address and length, instruction and register counts
//! With `--ref N`, it also runs the program on Turso's engine for N frames and writes OUT/ref.txt: one FNV-1a-64 hash
//! per frame (each ResultRow), plus frames/s on stderr. Copied into vdbecc/src/bin/ by build-export.sh.
//! VDBECC_HOST_MEM=1 runs Turso with the SQLite port's memory model (a host buffer behind native vdbe_blob_read /
//! vdbe_blob_write; turso-realtime.patch) for --ref timing only: prog.txt is not written then.
//!
//! usage: vdbe2sqlite OUT [--ref N] [--ram BYTES] [--entry NAME] [--arg N] --patch vdbe_wad=WAD files.ll...

use std::fmt::Write as _;
use std::sync::Arc;
use turso_core::vdbe::insn::Insn;
use turso_core::{Database, Func, MemoryIO, ScalarFunc, Value, IO};
use vdbecc::parse::link_modules;
use vdbecc::{compile, parse_module, CompileOpts};

fn fnv(b: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &x in b {
        h ^= x as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

fn hex(b: &[u8]) -> String {
    let mut s = String::with_capacity(b.len() * 2);
    for x in b {
        write!(s, "{x:02x}").unwrap();
    }
    s
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut opts = CompileOpts {
        ram_bytes: 64 << 20,
        entry: "vdbe_run".into(),
        dump_global: Some("vdbe_fb".into()),
        ..Default::default()
    };
    let mut out = None;
    let mut ref_frames = 0usize;
    let mut inputs = Vec::new();
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--ref" => ref_frames = it.next().unwrap().parse().unwrap(),
            "--ram" => opts.ram_bytes = it.next().unwrap().parse().unwrap(),
            "--entry" => opts.entry = it.next().unwrap().clone(),
            "--arg" => opts.entry_args.push(it.next().unwrap().parse().unwrap()),
            "--patch" => {
                let (name, file) = it.next().unwrap().split_once('=').unwrap();
                opts.patch_globals.push((name.into(), std::fs::read(file).expect("patch file")));
            }
            _ if out.is_none() => out = Some(a.clone()),
            _ => inputs.push(a.clone()),
        }
    }
    let out = out.expect("usage: vdbe2sqlite OUT [--ref N] ... files.ll");
    std::fs::create_dir_all(&out).unwrap();
    let modules = inputs
        .iter()
        .map(|f| parse_module(&std::fs::read_to_string(f).unwrap()).unwrap_or_else(|e| panic!("{f}: {e}")))
        .collect();
    let module = link_modules(modules).expect("link");

    let io: Arc<dyn IO> = Arc::new(MemoryIO::new());
    let db = Database::open_file(io, ":memory:").expect("open");
    let conn = db.connect().expect("connect");
    let t0 = std::time::Instant::now();
    let mut compiled = compile(&conn, &module, &opts).expect("compile");
    eprintln!("compiled in {:.1?}", t0.elapsed());

    // The RAM image as compile() stored it.
    let mut q = conn.prepare("SELECT ram FROM _vdbecc_mem WHERE id = 1").unwrap();
    let rows = q.run_collect_rows().unwrap();
    let Value::Blob(ram) = &rows[0][0] else { panic!("ram row") };
    std::fs::write(format!("{out}/ram.bin"), ram).unwrap();
    // VDBECC_HOST_MEM=1 (fair comparison): memory is a host buffer, not the blob row; the program is Turso-only then
    let host_mem = std::env::var("VDBECC_HOST_MEM").is_ok_and(|v| v == "1");
    if host_mem {
        turso_core::set_host_ram(ram.to_vec());
    }

    let prog = compiled.stmt.get_program().prepared().clone();
    let mut txt = String::new();
    let mut maxreg = 0usize;
    let mut r = |x: usize| {
        maxreg = maxreg.max(x);
        x
    };
    for (pc, (insn, _)) in prog.insns.iter().enumerate().filter(|_| !host_mem) {
        let line = match insn {
            Insn::Init { target_pc } => format!("Init {}", target_pc.as_offset_int()),
            Insn::Transaction { .. } => "Transaction".into(),
            Insn::Halt { err_code, description, description_reg, .. } => {
                assert!(description_reg.is_none());
                format!("Halt {err_code} {}", hex(description.as_bytes()))
            }
            Insn::Goto { target_pc } => format!("Goto {}", target_pc.as_offset_int()),
            Insn::Gosub { target_pc, return_reg } => format!("Gosub {} {}", r(*return_reg), target_pc.as_offset_int()),
            Insn::Return { return_reg, can_fallthrough } => format!("Return {} {}", r(*return_reg), *can_fallthrough as u8),
            Insn::Integer { value, dest } => format!("Integer {value} {}", r(*dest)),
            Insn::Real { value, dest } => format!("Real {:016x} {}", value.to_bits(), r(*dest)),
            Insn::Blob { value, dest } => format!("Blob {} {}", r(*dest), hex(value)),
            Insn::Copy { src_reg, dst_reg, extra_amount } => {
                r(*src_reg + extra_amount);
                r(*dst_reg + extra_amount);
                format!("Copy {src_reg} {dst_reg} {extra_amount}")
            }
            Insn::AddImm { register, value } => format!("AddImm {} {value}", r(*register)),
            Insn::Add { lhs, rhs, dest } => format!("Add {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::Subtract { lhs, rhs, dest } => format!("Subtract {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::Multiply { lhs, rhs, dest } => format!("Multiply {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::Divide { lhs, rhs, dest } => format!("Divide {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::Remainder { lhs, rhs, dest } => format!("Remainder {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::BitAnd { lhs, rhs, dest } => format!("BitAnd {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::BitOr { lhs, rhs, dest } => format!("BitOr {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::ShiftLeft { lhs, rhs, dest } => format!("ShiftLeft {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::ShiftRight { lhs, rhs, dest } => format!("ShiftRight {} {} {}", r(*lhs), r(*rhs), r(*dest)),
            Insn::Eq { lhs, rhs, target_pc, flags, collation }
            | Insn::Ne { lhs, rhs, target_pc, flags, collation }
            | Insn::Lt { lhs, rhs, target_pc, flags, collation }
            | Insn::Le { lhs, rhs, target_pc, flags, collation }
            | Insn::Gt { lhs, rhs, target_pc, flags, collation }
            | Insn::Ge { lhs, rhs, target_pc, flags, collation } => {
                assert!(collation.is_none() && !flags.has_jump_if_null() && !flags.has_nulleq(), "pc {pc}: cmp flags");
                let name = match insn {
                    Insn::Eq { .. } => "Eq",
                    Insn::Ne { .. } => "Ne",
                    Insn::Lt { .. } => "Lt",
                    Insn::Le { .. } => "Le",
                    Insn::Gt { .. } => "Gt",
                    _ => "Ge",
                };
                format!("{name} {} {} {}", r(*lhs), r(*rhs), target_pc.as_offset_int())
            }
            Insn::If { reg, target_pc, jump_if_null } => {
                format!("If {} {} {}", r(*reg), target_pc.as_offset_int(), *jump_if_null as u8)
            }
            Insn::IsNull { reg, target_pc } => format!("IsNull {} {}", r(*reg), target_pc.as_offset_int()),
            Insn::Cast { reg, affinity } => format!("Cast {} {}", r(*reg), *affinity as u8),
            Insn::RealAffinity { register } => format!("RealAffinity {}", r(*register)),
            Insn::Function { constant_mask, start_reg, dest, func } => {
                let name = match &func.func {
                    Func::Scalar(ScalarFunc::GetByte) => "get_byte",
                    Func::Scalar(ScalarFunc::SetByte) => "set_byte",
                    Func::External(x) => x.name.as_str(),   // vdbe_now_ms / vdbe_sleep_ms (realtime build)
                    f => panic!("pc {pc}: function {f:?}"),
                };
                assert_eq!(*constant_mask, 0);
                r(*start_reg + func.arg_count - 1);
                format!("Function {name} {start_reg} {} {}", func.arg_count, r(*dest))
            }
            Insn::BlobRead { offset, amount, dest, .. } => format!("BlobRead {} {} {}", r(*offset), r(*amount), r(*dest)),
            Insn::BlobWrite { offset, src, dest, .. } => format!("BlobWrite {} {} {}", r(*offset), r(*src), r(*dest)),
            Insn::Variable { index, dest } => format!("Variable {} {}", index.get(), r(*dest)),
            Insn::ResultRow { start_reg, count } => {
                r(*start_reg + count - 1);
                format!("ResultRow {start_reg} {count}")
            },
            Insn::OpenWrite { .. } => "OpenWrite".into(),
            Insn::SeekRowid { target_pc, .. } => format!("SeekRowid {}", target_pc.as_offset_int()),
            Insn::RegCopyOffset { src, base, offset_reg } => format!("RegCopyOffset {} {} {}", r(*src), r(*base), r(*offset_reg)),
            other => panic!("pc {pc}: unsupported instruction {other:?}"),
        };
        writeln!(txt, "{pc} {line}").unwrap();
    }
    std::fs::write(format!("{out}/prog.txt"), &txt).unwrap();
    let fb = compiled.globals["vdbe_fb"];
    let meta = format!(
        "ram {}\nfb_addr {fb}\nfb_len 256000\ninsns {}\nmax_registers {}\nmax_reg_used {maxreg}\nfunctions {}\n",
        opts.ram_bytes,
        prog.insns.len(),
        prog.max_registers,
        compiled.fn_map.len()
    );
    std::fs::write(format!("{out}/meta.txt"), &meta).unwrap();
    eprint!("{meta}");

    if ref_frames > 0 {
        let mut hashes = String::new();
        let t0 = std::time::Instant::now();
        let mut n = 0usize;
        let stmt = &mut compiled.stmt;
        while n < ref_frames {
            match stmt.step().expect("step") {
                turso_core::StepResult::Row => {
                    let row = stmt.row().unwrap();
                    let vals: Vec<&Value> = row.get_values().collect();
                    let Value::Blob(b) = vals[1] else { panic!("frame blob") };
                    writeln!(hashes, "{n} {:016x}", fnv(b)).unwrap();
                    if n % 50 == 0 {
                        eprintln!("frame {n} {:.1?}", t0.elapsed());
                    }
                    n += 1;
                }
                turso_core::StepResult::IO | turso_core::StepResult::Yield => stmt._io().step().unwrap(),
                turso_core::StepResult::Done => break,
                s => panic!("step: {s:?}"),
            }
        }
        let secs = t0.elapsed().as_secs_f64();
        eprintln!("turso: {n} frames in {secs:.2} s = {:.2} frames/s", n as f64 / secs);
        std::fs::write(format!("{out}/ref.txt"), hashes).unwrap();
    }
}
