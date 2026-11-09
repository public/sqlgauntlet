// backends/js/driver.mjs <binding> check|rows|info <db> <project.q> [id] [limit]: the driver's check and rows modes
// (src/driver.c) over a JavaScript SQLite binding, as an "exe" arm (run.py: <name>=exe:build/backends/js-<binding>).
// These arms test the binding, not the engine: how its values reach JavaScript (64-bit integers, reals, text decoding,
// blobs) and how it binds parameters, through the API an application would use. Bindings:
//   node     node:sqlite (Node.js's built-in, DatabaseSync; integers as BigInt: setReadBigInts)
//   bun      bun:sqlite (Database with safeIntegers)
//   sqljs    sql.js (asm/wasm build of SQLite; integers as BigInt: useBigInt); the database is read into memory
//   wasm     @sqlite.org/sqlite-wasm (the official build, oo1 API); the database is copied into its in-memory VFS
// Each binding evaluates the corpus's parameter literals itself ("SELECT <literal>") and binds what it got back, as
// the C driver does. Writes run inside BEGIN ... ROLLBACK. Values: a BigInt is an integer and a JS number a real
// (node, bun, sqljs); sqlite-wasm's oo1 returns numbers for both, so its column type decides. Text is hashed as the
// UTF-8 of the JavaScript string the binding produced (so a lossy decode shows), blobs as their bytes. The check and
// norm lines are byte-compatible with src/driver.c's (FNV-1a over each value's kind and bytes; %.*g normalisation).
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TP = path.resolve(HERE, '../../third_party/js');
const [binding, mode, dbPath, qFile, only, lim] = process.argv.slice(2);
const DIGITS = process.env.SG_DIGITS ? parseInt(process.env.SG_DIGITS, 10) : 12;
const INT = 1, FLOAT = 2, TEXT = 3, BLOB = 4, NUL = 5;

// ---- the bindings: open(path) -> { exec(sql), prepare(sql) -> { run(named, positional) -> rows }, changes(), version }
async function load(b) {
  if (b === 'node') {
    const { DatabaseSync } = await import('node:sqlite');
    return {
      version : () => `node:sqlite ${process.version}, SQLite ${new DatabaseSync(':memory:').prepare('SELECT sqlite_version() v').get().v}`,
      open(p) {
        const db = new DatabaseSync(p);
        return {
          db,
          exec : (s) => db.exec(s),
          prepare(s) {
            const st = db.prepare(s);
            st.setReadBigInts(true);
            st.setReturnArrays(true);
            st.setAllowBareNamedParameters(false);
            return { all : (named, pos, numbered) => (named ? st.all(named, ...(numbered || pos)) : st.all(...pos)), close() {} };
          },
          changes : () => Number(db.prepare('SELECT changes() c').get().c),
        };
      },
    };
  }
  if (b === 'bun') {
    const { Database } = await import('bun:sqlite');
    return {
      version : () => `bun:sqlite ${Bun.version}, SQLite ${new Database(':memory:').query('SELECT sqlite_version() v').get().v}`,
      open(p) {
        const db = new Database(p, { safeIntegers : true });
        return {
          db,
          exec : (s) => db.run(s),
          prepare(s) {
            const st = db.prepare(s);
            return {
              all : (named, pos) => (pos.length ? st.values(...pos)
                                     : named    ? st.values(named)
                                                : st.values()),
              close : () => st.finalize(),
            };
          },
          changes : () => Number(db.query('SELECT changes() c').get().c),
        };
      },
    };
  }
  if (b === 'sqljs') {
    const init = (await import(path.join(TP, 'sqljs/dist/sql-wasm.js'))).default;
    const SQL = await init({ locateFile : (f) => path.join(TP, 'sqljs/dist', f) });
    return {
      version : () => `sql.js ${JSON.parse(fs.readFileSync(path.join(TP, 'sqljs/package.json'))).version}, SQLite ${
        new SQL.Database().exec('SELECT sqlite_version()')[0].values[0][0]}`,
      open(p) {
        const db = new SQL.Database(p === ':memory:' ? undefined : fs.readFileSync(p));
        return {
          db,
          exec : (s) => db.exec(s),
          prepare(s) {
            const st = db.prepare(s);
            return {
              all(named, pos) {
                st.reset();
                if (pos.length) st.bind(pos); // (by index, named ones included)
                else if (named) st.bind(named);
                const rows = [];
                while (st.step()) rows.push(st.get(null, { useBigInt : true }));
                return rows;
              },
              close : () => st.free(),
            };
          },
          changes : () => db.getRowsModified(),
        };
      },
    };
  }
  if (b === 'wasm') {
    const init = (await import(path.join(TP, 'wasm/dist/node.mjs'))).default;
    const S = await init();
    const C = S.capi;
    return {
      version : () => `@sqlite.org/sqlite-wasm ${JSON.parse(fs.readFileSync(path.join(TP, 'wasm/package.json'))).version}, SQLite ${C.sqlite3_libversion()}`,
      open(p) {
        let f = ':memory:';
        const bytes = p === ':memory:' ? null : new Uint8Array(fs.readFileSync(p));
        if (bytes && bytes.length) {
          f = '/sg.db';
          C.sqlite3_js_posix_create_file(f, bytes);
        }
        const db = new S.oo1.DB(f);
        return {
          db,
          exec : (s) => db.exec(s),
          prepare(s) {
            const st = db.prepare(s);
            return {
              all(named, pos) {
                st.reset(true);
                if (pos.length) pos.forEach((v, i) => st.bind(i + 1, v)); // (by index, named ones included)
                else if (named)
                  for (const [k, v] of Object.entries(named)) st.bind(k, v);
                const rows = [];
                while (st.step()) {
                  const r = [];
                  for (let i = 0; i < st.columnCount; i++) {
                    const t = C.sqlite3_column_type(st.pointer, i);
                    r.push(t === C.SQLITE_INTEGER ? BigInt(st.get(i, C.SQLITE_INTEGER)) : st.get(i));
                  }
                  rows.push(r);
                }
                return rows;
              },
              close : () => st.finalize(),
            };
          },
          changes : () => db.changes(),
        };
      },
    };
  }
  throw new Error(`driver.mjs: no binding ${b} (node bun sqljs wasm)`);
}

// ---- hashing, as src/driver.c: FNV-1a 64 over bytes (two 32-bit halves), the multiset sum in BigInt
const enc = new TextEncoder();
const scratch = new DataView(new ArrayBuffer(8));
const S8 = new Uint8Array(scratch.buffer);
class H {
  constructor() {
    this.hi = 0x14650fb0;
    this.lo = 0x739d0383;
  } // (driver.c's offset basis 1469598103934665603: FNV's, its last digit dropped)
  bytes(u8) {
    let hi = this.hi, lo = this.lo;
    for (let i = 0; i < u8.length; i++) {
      lo = (lo ^ u8[i]) >>> 0;
      const t = lo * 0x1b3; // (h * 0x100000001b3 = h * 0x1b3 + (h << 40), mod 2^64)
      const nlo = t >>> 0;
      hi = (Math.imul(hi, 0x1b3) + Math.floor(t / 4294967296) + (lo << 8)) >>> 0;
      lo = nlo;
    }
    this.hi = hi;
    this.lo = lo;
    return this;
  }
  i32(x) {
    scratch.setInt32(0, x, true);
    return this.bytes(S8.subarray(0, 4));
  }
  i64(b) {
    scratch.setBigInt64(0, BigInt.asIntN(64, b), true);
    return this.bytes(S8);
  }
  f64(d) {
    scratch.setFloat64(0, d, true);
    return this.bytes(S8);
  }
  big() { return (BigInt(this.hi) << 32n) | BigInt(this.lo); }
}
const M64 = (1n << 64n) - 1n;
const mix = (rh) => (rh * 0x9e3779b97f4a7c15n + (rh >> 29n)) & M64;
const hex = (b) => b.toString(16).padStart(16, '0');

function fmtG(d, p) { // C's %.*g
  if (Number.isNaN(d)) return d < 0 ? '-nan' : 'nan';
  if (!Number.isFinite(d)) return d < 0 ? '-inf' : 'inf';
  if (d === 0) return Object.is(d, -0) ? '-0' : '0';
  const e = d.toExponential(p - 1);
  const X = parseInt(e.slice(e.indexOf('e') + 1), 10);
  const strip = (s) => (s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s);
  if (X < -4 || X >= p) {
    const [m, x] = e.split('e');
    const n = Math.abs(X);
    return `${strip(m)}e${X < 0 ? '-' : '+'}${n < 10 ? '0' : ''}${n}`;
  }
  return strip(d.toFixed(p - 1 - X));
}
const TWO53 = 9007199254740992;
function kindOf(v) {
  if (v === null || v === undefined) return NUL;
  if (typeof v === 'bigint') return INT;
  if (typeof v === 'number') return FLOAT;
  if (typeof v === 'string') return TEXT;
  if (v instanceof Uint8Array || ArrayBuffer.isView(v)) return BLOB;
  if (typeof v === 'boolean') return INT;
  return TEXT;
}
const bytesOf = (v) => (typeof v === 'string' ? enc.encode(v) : new Uint8Array(v.buffer, v.byteOffset, v.byteLength));
function nval(h, v, t) {
  h.i32(t === FLOAT ? INT : t);
  if (t === INT) {
    const b = typeof v === 'boolean' ? BigInt(+v) : v;
    const d = Number(b);
    if (d >= -TWO53 && d <= TWO53) h.i64(b);
    else h.bytes(enc.encode(fmtG(d, DIGITS)));
  } else if (t === FLOAT) {
    if (v === Math.floor(v) && Math.abs(v) <= TWO53) h.i64(BigInt(v));
    else h.bytes(enc.encode(fmtG(v, DIGITS)));
  } else if (t === TEXT || t === BLOB) h.bytes(bytesOf(v));
  return h;
}
function ntext(v, t) {
  if (t === NUL) return 'NULL';
  if (t === INT) return String(typeof v === 'boolean' ? +v : v);
  if (t === FLOAT) return v === Math.floor(v) && Math.abs(v) <= TWO53 ? BigInt(v).toString() : fmtG(v, DIGITS);
  if (t === TEXT) return `'${v.slice(0, 253).replace(/[\t\n]/g, ' ')}'`;
  return `x'${Buffer.from(bytesOf(v)).toString('hex')}'`;
}

// ---- the corpus file
function parse(text) {
  const qs = [], pragma = [];
  for (const line of text.split('\n')) {
    if (line.startsWith('@@Q ')) {
      const [id, ordered, write, tags] = line.slice(4).split(' ');
      qs.push({ id, ordered : +ordered, write : +write, tags : (tags || '').split(','), sql : null, params : [] });
    } else if (line.startsWith('@@X ') && !qs.length) pragma.push(line.slice(4));
    else if (line.startsWith('@@P ') && qs.length) {
      const s = line.slice(4), k = s.indexOf(' ');
      if (k > 0) qs[qs.length - 1].params.push([ s.slice(0, k), s.slice(k + 1) ]);
    } else if (qs.length) {
      const q = qs[qs.length - 1];
      q.sql = q.sql === null ? line : `${q.sql}\n${line}`;
    }
  }
  for (const q of qs)
    if (q.sql !== null) q.sql = q.sql.replace(/\n$/, '');
  return { qs, pragma };
}

// ---- one execution (as exec1 in driver.c)
const errmsg = (e) => String((e && e.message) || e).replace(/[\t\n]/g, ' ');
function exec1(c, q, st, args, rowsOut) {
  if (q.write) c.exec('BEGIN');
  const r = { rows : 0, h : 0n, hs : 0n, nh : 0n, nhs : 0n, changes : 0, err : '' };
  let rows = [];
  try {
    rows = st.all(args.named, args.pos, args.numbered);
  } catch (e) { r.err = errmsg(e); }
  const H0 = new H(), N0 = new H();
  for (const row of rows) {
    const rh = new H(), nrh = new H();
    const out = [];
    for (const v of row) {
      const t = kindOf(v);
      rh.i32(t);
      if (t === INT) rh.i64(typeof v === 'boolean' ? BigInt(+v) : v);
      else if (t === FLOAT) rh.f64(v);
      else if (t === TEXT || t === BLOB) rh.bytes(bytesOf(v));
      nval(nrh, v, t);
      if (rowsOut && r.rows < rowsOut.limit) out.push(ntext(v, t));
    }
    if (rowsOut && r.rows < rowsOut.limit) rowsOut.lines.push(row.length ? out.join('\t') : '(no columns)');
    const a = rh.big(), b = nrh.big();
    scratch.setBigUint64(0, a, true);
    H0.bytes(S8);
    r.hs = (r.hs + mix(a)) & M64;
    scratch.setBigUint64(0, b, true);
    N0.bytes(S8);
    r.nhs = (r.nhs + mix(b)) & M64;
    r.rows++;
  }
  r.h = H0.big();
  r.nh = N0.big();
  try {
    r.changes = c.changes();
  } catch { r.changes = -1; }
  if (q.write) {
    try {
      c.exec('ROLLBACK');
    } catch {}
  }
  return r;
}
function paramsOf(sql) { // the parameters the SQL text uses (outside strings, quoted names and comments): the bound
  // ones the C driver would find through sqlite3_bind_parameter_index/count; the corpus may list more than are used
  const named = new Map(); // (name -> its index: SQLite gives a name the next free index where it first appears)
  let n = 0, i = 0;
  while (i < sql.length) {
    const ch = sql[i];
    if (ch === "'" || ch === '"' || ch === '`' || ch === '[') {
      const end = ch === '[' ? ']' : ch;
      i++;
      while (i < sql.length && !(sql[i] === end && sql[i + 1] !== end)) i += sql[i] === end ? 2 : 1;
      i++;
    } else if (ch === '-' && sql[i + 1] === '-') {
      while (i < sql.length && sql[i] !== '\n') i++;
    } else if (ch === '/' && sql[i + 1] === '*') {
      const e = sql.indexOf('*/', i + 2);
      i = e < 0 ? sql.length : e + 2;
    } else if (ch === '?') {
      let j = i + 1;
      while (j < sql.length && sql[j] >= '0' && sql[j] <= '9') j++;
      n = j > i + 1 ? Math.max(n, parseInt(sql.slice(i + 1, j), 10)) : n + 1;
      i = j;
    } else if ((ch === ':' || ch === '@' || ch === '$') && /[A-Za-z_]/.test(sql[i + 1] || '')) {
      let j = i + 1;
      while (j < sql.length && /[A-Za-z0-9_]/.test(sql[j])) j++;
      const k = sql.slice(i, j);
      if (!named.has(k)) named.set(k, ++n);
      i = j;
    } else i++;
  }
  return { named, count : n };
}
function evalParams(c, q) { // each literal through the binding: its value, as an app would hand it over
  const named = {}, pos = [];
  for (const [name, lit] of q.params) {
    let v;
    try {
      const st = c.prepare(`SELECT ${lit}`);
      const rows = st.all(null, []);
      st.close();
      if (!rows.length) continue;
      v = rows[0][0];
    } catch { continue; } // (left unbound: the engine cannot evaluate the literal)
    if (name[0] === '?') pos[parseInt(name.slice(1), 10) - 1] = v;
    else named[name] = v;
  }
  const use = paramsOf(q.sql); // (only what the statement has: a binding refuses extra values, the C driver skips them)
  for (const k of Object.keys(named))
    if (!use.named.has(k)) delete named[k];
  const full = []; // (every parameter by its SQLite index, numbered and named)
  for (let i = 0; i < use.count; i++) full[i] = pos[i] === undefined ? null : pos[i];
  for (const [k, ix] of use.named)
    if (k in named) full[ix - 1] = named[k];
  const nix = new Set(use.named.values());
  const numbered = full.filter((_, i) => !nix.has(i + 1)); // (node binds positional values to the unnamed ones, in order)
  return { named : Object.keys(named).length ? named : null, pos : full, numbered };
}

async function main() {
  const B = await load(binding);
  if (mode === 'info') {
    console.log(`info\tjs-${binding}\tsqlite\t${B.version()}`);
    return 0;
  }
  if (!['check', 'rows'].includes(mode)) {
    console.error(`driver.mjs: mode ${mode}: check, rows or info`);
    return 2;
  }
  const c = B.open(dbPath);
  const { qs, pragma } = parse(fs.readFileSync(qFile, 'utf8'));
  for (const p of pragma) c.exec(p);
  const out = [];
  const flush = () => {
    if (out.length) {
      fs.writeSync(1, out.join(''));
      out.length = 0;
    }
  };
  for (const q of qs) {
    if (only && q.id !== only) continue;
    const args = evalParams(c, q);
    if (mode === 'rows') {
      const ro = { limit : lim ? parseInt(lim, 10) : 20, lines : [] };
      let r = { rows : 0, err : '' };
      try {
        const st = c.prepare(q.sql);
        r = exec1(c, q, st, args, ro);
        st.close();
      } catch (e) { r.err = `prepare: ${errmsg(e)}`; }
      out.push(`rows\t${q.id}\n`, ...ro.lines.map((l) => `${l}\n`), `rows\t${q.id}\t${r.rows} rows\t${r.err || '-'}\n`);
      continue;
    }
    const wall = q.tags.includes('wallclock');
    let status = 'ok', runs = 0, st = null, r0 = { rows : 0, changes : 0, h : 0n, hs : 0n, nh : 0n, nhs : 0n, err : '' };
    try {
      st = c.prepare(q.sql);
    } catch (e) {
      status = 'err';
      r0.err = `prepare: ${errmsg(e)}`;
    }
    if (st) {
      for (let k = 0; k < 8; k++) {
        if (k >= 6) {
          st.close();
          st = null;
          try {
            st = c.prepare(q.sql);
          } catch {
            status = 'unstable';
            break;
          }
        }
        const r = exec1(c, q, st, args, null);
        runs++;
        if (!k) {
          r0 = r;
          continue;
        }
        if (r.rows !== r0.rows || (!wall && (q.ordered ? r.h !== r0.h : r.hs !== r0.hs)) || r.changes !== r0.changes || r.err !== r0.err) {
          status = 'unstable';
          r0.err += ` [run ${k}: ${r.rows} rows ${hex(r.h)} ${r.err}]`;
          break;
        }
      }
      if (status === 'ok' && r0.err) status = 'err';
      if (st) st.close();
    }
    const e = r0.err.replace(/[\t\n]/g, ' ');
    out.push(`check\t${q.id}\t${status}\t${r0.rows}\t${r0.changes}\t${hex(r0.h)}\t${hex(r0.hs)}\t0/${runs}\t${e || '-'}\n`);
    out.push(`norm\t${q.id}\t${hex(r0.nh)}\t${hex(r0.nhs)}\n`);
    flush();
  }
  flush();
  return 0;
}
main().then((rc) => process.exit(rc), (e) => {
  console.error(`driver.mjs: ${e.stack || e}`);
  process.exit(1);
});
