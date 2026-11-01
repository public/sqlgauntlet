/* doomsql.c: run Turso's Doom VDBE program (exported by vdbe2sqlite into OUT/) on SQLite's own VDBE.
 *
 * The program is translated instruction by instruction into a real Vdbe (sqlite3VdbeAddOp*, sqlite3VdbeMakeReady, as
 * sqlite3_blob_open builds its own program), so sqlite3_step runs it through sqlite3VdbeExec and qc sees an ordinary
 * prepared statement. Memory is a host buffer behind four scalar functions with Turso's semantics: vdbe_blob_read,
 * vdbe_blob_write (its BlobRead/BlobWrite opcodes) and get_byte/set_byte (PostgreSQL bytea). See DESIGN.md.
 *
 *   doomsql OUT [stock|qc|qvm] [--frames N] [--runs K] [--ppm DIR] [--every K] [--quiet]
 *     stock  sqlite3_step (the interpreter)
 *     qc     sqlite3_qc + sqlite3_qc_exec (the plan tiers decline, then qvm); falls back to sqlite3_step
 *     qvm    the same with QC_VM_ONLY=1
 *   (qc/qvm run the whole program whatever --frames says: time whole-program exports, out200/out400, for A/B.)
 *   Prints "frame N HASH" (FNV-1a-64 of each 256000-byte frame) for the first N rows and the time per mode. --runs K
 *   executes the statement K times (RAM reset each time; qvm builds its loop regions on the third run) and compares
 *   every later run's frames with the first's.
 *
 * Build: build.sh (#include "sqlite3.c" from cursed-sqlite-jit's build/gen; -DWITH_QC adds qc.c). */
#include "sqlite3.c"
#ifdef WITH_QC
#include "qc.c"
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static u8 *g_ram; static i64 g_ramsz;
/* live input (doomplay.c): Variable ?i becomes vdbe_input(i), read from g_input[] which the host refreshes between
   frames (SQLite can't rebind a running statement, which is how Turso's browser demo feeds keys) */
static int g_live_input; static i64 g_input[11];

static void die(const char *m) { fprintf(stderr, "doomsql: %s\n", m); exit(1); }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static u64 fnv(const u8 *b, int n) { u64 h = 0xcbf29ce484222325ULL; for (int i = 0; i < n; i++) { h ^= b[i]; h *= 0x100000001b3ULL; } return h; }

/* ---- the memory functions (Turso's semantics; errors as Turso raises them) ---- */
static int int_arg(sqlite3_value *v, i64 *out) {   /* exec_cast("INT"); NULL -> no value */
  if (sqlite3_value_type(v) == SQLITE_NULL) return 0;
  *out = sqlite3_value_int64(v); return 1;
}
static void f_blob_read(sqlite3_context *c, int n, sqlite3_value **a) {
  i64 off, len; (void)n;
  if (!int_arg(a[0], &off) || !int_arg(a[1], &len)) { sqlite3_result_error(c, "blob_read: null", -1); return; }
  if (off < 0 || len < 0 || off + len > g_ramsz) { sqlite3_result_error(c, "blob_read: out of range", -1); return; }
  sqlite3_result_blob(c, g_ram + off, (int)len, SQLITE_TRANSIENT);
}
static void f_blob_write(sqlite3_context *c, int n, sqlite3_value **a) {
  i64 off; (void)n;
  if (!int_arg(a[0], &off) || sqlite3_value_type(a[1]) != SQLITE_BLOB) {
    char e[96]; snprintf(e, sizeof e, "blob_write: args (types %d %d, iOp %d)", sqlite3_value_type(a[0]), sqlite3_value_type(a[1]), c->iOp);
    sqlite3_result_error(c, e, -1); return;
  }
  int len = sqlite3_value_bytes(a[1]); const void *p = sqlite3_value_blob(a[1]);
  if (off < 0 || off + len > g_ramsz) { sqlite3_result_error(c, "blob_write: out of range", -1); return; }
  if (len) memcpy(g_ram + off, p, (size_t)len);
  sqlite3_result_int(c, 1);
}
static void f_get_byte(sqlite3_context *c, int n, sqlite3_value **a) {
  i64 off; (void)n;
  if (!int_arg(a[1], &off) || sqlite3_value_type(a[0]) == SQLITE_NULL) { sqlite3_result_null(c); return; }
  int len = sqlite3_value_bytes(a[0]); const u8 *p = sqlite3_value_blob(a[0]);
  if (off < 0 || off >= len) { sqlite3_result_error(c, "get_byte: index out of valid range", -1); return; }
  sqlite3_result_int(c, p[off]);
}
/* the realtime build's clock (make-realtime-ll.py): DG_GetTicksMs and DG_SleepMs */
static void f_now_ms(sqlite3_context *c, int n, sqlite3_value **a) {
  static double t0; (void)n; (void)a; double t = now(); if (!t0) t0 = t;
  sqlite3_result_int64(c, (i64)((t - t0) * 1000));
}
static void f_sleep_ms(sqlite3_context *c, int n, sqlite3_value **a) {
  (void)n; i64 ms = sqlite3_value_int64(a[0]); if (ms > 0 && ms <= 1000) usleep((useconds_t)(ms * 1000));
  sqlite3_result_null(c);
}
static void f_set_byte(sqlite3_context *c, int n, sqlite3_value **a) {
  i64 off, v; (void)n;
  if (!int_arg(a[1], &off) || !int_arg(a[2], &v) || sqlite3_value_type(a[0]) == SQLITE_NULL) { sqlite3_result_null(c); return; }
  int len = sqlite3_value_bytes(a[0]); const u8 *p = sqlite3_value_blob(a[0]);
  if (off < 0 || off >= len) { sqlite3_result_error(c, "set_byte: index out of valid range", -1); return; }
  u8 *q = sqlite3_malloc(len ? len : 1); if (!q) { sqlite3_result_error_nomem(c); return; }
  memcpy(q, p, (size_t)len); q[off] = (u8)v;
  sqlite3_result_blob(c, q, len, sqlite3_free);
}

/* ---- the exported program ---- */
typedef struct Ins { char op[16]; i64 a[4]; char *s; } Ins;   /* s: hex payload (Blob, Halt) or function name */
static Ins *P; static int nP;

static void load_prog(const char *path) {
  FILE *f = fopen(path, "r"); if (!f) die("open prog.txt");
  size_t cap = 1 << 20; P = malloc(cap * sizeof *P); char *line = 0; size_t lc = 0; ssize_t k;
  while ((k = getline(&line, &lc, f)) > 0) {
    if (line[k - 1] == '\n') line[--k] = 0;
    if ((size_t)nP == cap) P = realloc(P, (cap *= 2) * sizeof *P);
    Ins *I = &P[nP]; memset(I, 0, sizeof *I);
    char *t = strtok(line, " "); if (atoi(t) != nP) die("prog.txt: pc out of order");
    t = strtok(0, " "); snprintf(I->op, sizeof I->op, "%s", t);
    int na = 0;
    while ((t = strtok(0, " "))) {
      int isnum = (t[0] == '-' || (t[0] >= '0' && t[0] <= '9'));
      if (!strcmp(I->op, "Real") && na == 0) { I->a[na++] = (i64)strtoull(t, 0, 16); continue; }
      if (!strcmp(I->op, "Function") && !I->s) { I->s = strdup(t); continue; }
      if (!strcmp(I->op, "Halt") && na == 1) { I->s = strdup(t); continue; }
      if (!strcmp(I->op, "Blob") && na == 1) { I->s = strdup(t); continue; }
      if (!isnum || na == 4) die("prog.txt: bad operand");
      I->a[na++] = strtoll(t, 0, 10);
    }
    nP++;
  }
  free(line); fclose(f);
}
static u8 *unhex(const char *s, int *n) {
  int len = s ? (int)strlen(s) / 2 : 0; u8 *b = sqlite3_malloc(len ? len : 1);
  for (int i = 0; i < len; i++) { unsigned x; sscanf(s + 2 * i, "%2x", &x); b[i] = (u8)x; }
  *n = len; return b;
}

static int is(const Ins *I, const char *op) { return !strcmp(I->op, op); }
/* SQLite ops emitted for one Turso instruction (pc remapping) */
static int width(int pc) {
  const Ins *I = &P[pc];
  if (is(I, "BlobRead") || is(I, "BlobWrite")) return 3;
  if (is(I, "Variable") && g_live_input) return 2;
  if (is(I, "Function") && I->a[2] >= I->a[0] && I->a[2] < I->a[0] + I->a[1]) return 2;   /* result overlaps the arguments */
  if (pc + 1 < nP && is(&P[pc + 1], "RegCopyOffset")) return -1;   /* the zeroing loop's test: sized later */
  return 1;
}

static sqlite3_stmt *build(sqlite3 *db, int maxreg) {
  /* registers: Turso r -> r + 1; A0, A1: the argument window for the blob functions */
  int A0 = maxreg + 2, A1 = maxreg + 3, A2 = maxreg + 4, nMem = maxreg + 5;
  int *map = malloc(sizeof(int) * (size_t)(nP + 1)), at = 0, zbase = 0, zcount = 0, nz = 0;
  for (int pc = 0; pc < nP; pc++) {
    map[pc] = at; int w = width(pc);
    if (w < 0) {   /* "Ge s2 s3 done; RegCopyOffset src base s2": registers[base .. base+count) = 0, count from "Integer count s3" */
      const Ins *G = &P[pc], *R = &P[pc + 1]; if (!is(G, "Ge") || R->a[2] != G->a[0]) die("zeroing loop shape");
      int found = 0; for (int q = pc - 1; q >= 0 && q > pc - 8; q--) if (is(&P[q], "Integer") && P[q].a[1] == G->a[1]) { zcount = (int)P[q].a[0]; found = 1; break; }
      if (!found) die("zeroing loop count");
      zbase = (int)R->a[1] + 1; nz = 1; for (int k = 1; k < zcount; k *= 2) nz++;
      w = nz + 1;
    }
    at += w;
  }
  map[nP] = at;

  Parse sParse; sqlite3ParseObjectInit(&sParse, db);
  Vdbe *v = sqlite3GetVdbe(&sParse); if (!v) die("vdbe");
  FuncDef *fGet = sqlite3FindFunction(db, "get_byte", 2, SQLITE_UTF8, 0), *fSet = sqlite3FindFunction(db, "set_byte", 3, SQLITE_UTF8, 0);
  FuncDef *fRd = sqlite3FindFunction(db, "vdbe_blob_read", 2, SQLITE_UTF8, 0), *fWr = sqlite3FindFunction(db, "vdbe_blob_write", 2, SQLITE_UTF8, 0);
  if (!fGet || !fSet || !fRd || !fWr) die("functions");
#define R(x) ((int)(x) + 1)
#define T(x) (map[(int)(x)])
  for (int pc = 0; pc < nP; pc++) {
    const Ins *I = &P[pc]; const i64 *a = I->a;
    if (sqlite3VdbeCurrentAddr(v) != (pc ? map[pc] : 1)) die("pc map");
    if (pc + 1 < nP && is(&P[pc + 1], "RegCopyOffset")) {
      sqlite3VdbeAddOp2(v, OP_Integer, 0, zbase);
      for (int k = 1; k < zcount; k *= 2) { int m = k < zcount - k ? k : zcount - k; sqlite3VdbeAddOp3(v, OP_Copy, zbase, zbase + k, m - 1); }
      sqlite3VdbeAddOp2(v, OP_Goto, 0, T(I->a[2]));
      continue;
    }
    if (is(I, "Init")) { if (pc) die("Init"); sqlite3VdbeChangeP2(v, 0, T(a[0])); }   /* sqlite3GetVdbe emitted the OP_Init */
    else if (is(I, "Goto")) sqlite3VdbeAddOp2(v, OP_Goto, 0, T(a[0]));
    else if (is(I, "Transaction") || is(I, "OpenWrite") || is(I, "SeekRowid") || is(I, "RegCopyOffset")) sqlite3VdbeAddOp0(v, OP_Noop);
    else if (is(I, "Halt")) {
      int n; u8 *msg = unhex(I->s, &n);
      if (a[0] == 0) { sqlite3VdbeAddOp0(v, OP_Halt); sqlite3_free(msg); }
      else { char *z = sqlite3DbMallocRawNN(db, (u64)n + 1); memcpy(z, msg, (size_t)n); z[n] = 0; sqlite3_free(msg);
             sqlite3VdbeAddOp4(v, OP_Halt, SQLITE_ERROR, OE_Abort, 0, z, P4_DYNAMIC); }
    }
    else if (is(I, "Gosub")) sqlite3VdbeAddOp2(v, OP_Gosub, R(a[0]), T(a[1]));
    else if (is(I, "Return")) sqlite3VdbeAddOp3(v, OP_Return, R(a[0]), 0, (int)a[1]);
    else if (is(I, "Integer")) {
      if (a[0] == (int)a[0]) sqlite3VdbeAddOp2(v, OP_Integer, (int)a[0], R(a[1]));
      else sqlite3VdbeAddOp3(v, OP_Int64, (int)(u32)a[0], R(a[1]), (int)(u32)((u64)a[0] >> 32));   /* P1 low, P3 high (3.54) */
    }
    else if (is(I, "Real")) sqlite3VdbeAddOp3(v, OP_Real, (int)(u32)a[0], R(a[1]), (int)(u32)((u64)a[0] >> 32));   /* the bits, as Int64 */
    else if (is(I, "Blob")) { int n; u8 *b = unhex(I->s, &n); u8 *z = sqlite3DbMallocRawNN(db, (u64)n + 1); memcpy(z, b, (size_t)n); sqlite3_free(b);
                              sqlite3VdbeAddOp4(v, OP_Blob, n, R(a[0]), 0, (char *)z, P4_DYNAMIC); }
    else if (is(I, "Copy")) sqlite3VdbeAddOp3(v, OP_Copy, R(a[0]), R(a[1]), (int)a[2]);
    else if (is(I, "AddImm")) { if (a[1] != (int)a[1]) die("AddImm range"); sqlite3VdbeAddOp2(v, OP_AddImm, R(a[0]), (int)a[1]); }
    else if (is(I, "Add") || is(I, "Subtract") || is(I, "Multiply") || is(I, "Divide") || is(I, "Remainder") ||
             is(I, "BitAnd") || is(I, "BitOr") || is(I, "ShiftLeft") || is(I, "ShiftRight")) {
      int op = is(I, "Add") ? OP_Add : is(I, "Subtract") ? OP_Subtract : is(I, "Multiply") ? OP_Multiply : is(I, "Divide") ? OP_Divide :
               is(I, "Remainder") ? OP_Remainder : is(I, "BitAnd") ? OP_BitAnd : is(I, "BitOr") ? OP_BitOr : is(I, "ShiftLeft") ? OP_ShiftLeft : OP_ShiftRight;
      sqlite3VdbeAddOp3(v, op, R(a[1]), R(a[0]), R(a[2]));   /* r[P3] = r[P2] op r[P1]: P2 = lhs, P1 = rhs */
    }
    else if (is(I, "Eq") || is(I, "Ne") || is(I, "Lt") || is(I, "Le") || is(I, "Gt") || is(I, "Ge")) {
      int op = is(I, "Eq") ? OP_Eq : is(I, "Ne") ? OP_Ne : is(I, "Lt") ? OP_Lt : is(I, "Le") ? OP_Le : is(I, "Gt") ? OP_Gt : OP_Ge;
      sqlite3VdbeAddOp3(v, op, R(a[1]), T(a[2]), R(a[0]));   /* jump if r[P3] op r[P1]: P3 = lhs */
    }
    else if (is(I, "If")) sqlite3VdbeAddOp3(v, OP_If, R(a[0]), T(a[1]), (int)a[2]);
    else if (is(I, "IsNull")) sqlite3VdbeAddOp2(v, OP_IsNull, R(a[0]), T(a[1]));
    else if (is(I, "Cast")) sqlite3VdbeAddOp2(v, OP_Cast, R(a[0]), SQLITE_AFF_BLOB + (int)a[1]);
    else if (is(I, "RealAffinity")) sqlite3VdbeAddOp1(v, OP_RealAffinity, R(a[0]));
    else if (is(I, "Function")) {
      FuncDef *f = !strcmp(I->s, "get_byte") ? fGet : !strcmp(I->s, "set_byte") ? fSet : sqlite3FindFunction(db, I->s, (int)a[1], SQLITE_UTF8, 0);
      if (!f || f->nArg != a[1]) { fprintf(stderr, "function %s/%d\n", I->s, (int)a[1]); die("Function not registered"); }
      if (width(pc) == 2) {   /* vdbe.c's OP_Function sets the result register to NULL before the call: result to A2, then moved */
        sqlite3VdbeAddFunctionCall(&sParse, 0, R(a[0]), A2, (int)a[1], f, 0);
        sqlite3VdbeAddOp3(v, OP_Move, A2, R(a[2]), 1);
      } else sqlite3VdbeAddFunctionCall(&sParse, 0, R(a[0]), R(a[2]), (int)a[1], f, 0);
    }
    else if (is(I, "BlobRead") || is(I, "BlobWrite")) {
      sqlite3VdbeAddOp2(v, OP_SCopy, R(a[0]), A0); sqlite3VdbeAddOp2(v, OP_SCopy, R(a[1]), A1);
      sqlite3VdbeAddFunctionCall(&sParse, 0, A0, R(a[2]), 2, is(I, "BlobRead") ? fRd : fWr, 0);
    }
    else if (is(I, "Variable") && g_live_input) {
      sqlite3VdbeAddOp2(v, OP_Integer, (int)a[0], A0);
      sqlite3VdbeAddFunctionCall(&sParse, 0, A0, R(a[1]), 1, sqlite3FindFunction(db, "vdbe_input", 1, SQLITE_UTF8, 0), 0);
    }
    else if (is(I, "Variable")) sqlite3VdbeAddOp2(v, OP_Variable, (int)a[0], R(a[1]));
    else if (is(I, "ResultRow")) sqlite3VdbeAddOp2(v, OP_ResultRow, R(a[0]), (int)a[1]);
    else { fprintf(stderr, "pc %d: %s\n", pc, I->op); die("unsupported instruction"); }
  }
  if (sqlite3VdbeCurrentAddr(v) != map[nP]) die("pc map end");
  sqlite3VdbeSetNumCols(v, 2);
  sqlite3VdbeSetColName(v, 0, COLNAME_NAME, "ret", SQLITE_STATIC);
  sqlite3VdbeSetColName(v, 1, COLNAME_NAME, "frame", SQLITE_STATIC);
  sParse.nMem = nMem; sParse.nVar = 10; sParse.nTab = 0;
  sqlite3VdbeMakeReady(v, &sParse);
  sqlite3ParseObjectReset(&sParse);
  if (db->mallocFailed) die("out of memory");
  free(map);
  fprintf(stderr, "doomsql: %d Turso instructions -> %d SQLite ops, %d registers\n", nP, v->nOp, nMem);
  return (sqlite3_stmt *)v;
}

/* ---- frames ---- */
static int g_nf, g_max, g_quiet, g_every = 1, g_run, g_bad, g_nh; static const char *g_ppm; static u64 *g_hash;
static void frame(const u8 *fb, int n) {
  if (g_nf >= g_max) { g_nf++; return; }
  if (n != 256000) { fprintf(stderr, "frame %d: %d bytes\n", g_nf, n); die("frame size"); }
  u64 h = fnv(fb, n);
  if (g_run) {   /* a later run: the same frames as the first */
    if (g_nf >= g_nh || g_hash[g_nf] != h) { if (g_bad++ < 5) fprintf(stderr, "run %d frame %d differs\n", g_run, g_nf); }
    g_nf++; return;
  }
  if (!(g_nh & (g_nh + 1))) g_hash = realloc(g_hash, sizeof *g_hash * (size_t)(2 * g_nh + 2));
  g_hash[g_nh++] = h;
  if (!g_quiet) printf("frame %d %016llx\n", g_nf, (unsigned long long)h);
  if (g_ppm && g_nf % g_every == 0) {   /* doomgeneric pixels: 0x00RRGGBB, little-endian */
    char path[512]; snprintf(path, sizeof path, "%s/f%05d.ppm", g_ppm, g_nf);
    FILE *f = fopen(path, "wb"); if (!f) die("ppm");
    fprintf(f, "P6 320 200 255\n");
    for (int i = 0; i < 64000; i++) { u8 px[3] = { fb[4 * i + 2], fb[4 * i + 1], fb[4 * i] }; fwrite(px, 1, 3, f); }
    fclose(f);
  }
  g_nf++;
}
#ifdef WITH_QC
static int qc_rows(void *ctx, int nrow, int ncol, const QcVal *vals) {
  (void)ctx;
  for (int r = 0; r < nrow; r++) {
    const QcVal *x = &vals[r * ncol + 1];
    if (x->type != SQLITE_BLOB) die("qc row: not a blob");
    frame(x->z, x->n);
  }
  return SQLITE_OK;
}
#endif

static char *slurp(const char *path, i64 *n) {
  FILE *f = fopen(path, "rb"); if (!f) return 0;
  fseek(f, 0, SEEK_END); *n = ftell(f); fseek(f, 0, SEEK_SET);
  char *b = malloc((size_t)*n + 1); if (fread(b, 1, (size_t)*n, f) != (size_t)*n) die("read"); b[*n] = 0; fclose(f); return b;
}
static i64 meta(const char *m, const char *key) { const char *p = strstr(m, key); if (!p) die(key); return strtoll(p + strlen(key) + 1, 0, 10); }

#ifndef DOOMSQL_NO_MAIN
int main(int argc, char **argv) {
  if (argc < 2) die("usage: doomsql OUT [stock|qc|qvm] [--frames N] [--ppm DIR] [--every K] [--quiet]");
  const char *out = argv[1], *mode = "stock"; int runs = 1; g_max = 1 << 30;
  for (int i = 2; i < argc; i++) {
    if (!strcmp(argv[i], "--frames")) g_max = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--ppm")) g_ppm = argv[++i];
    else if (!strcmp(argv[i], "--every")) g_every = atoi(argv[++i]);
    else if (!strcmp(argv[i], "--quiet")) g_quiet = 1;
    else if (!strcmp(argv[i], "--runs")) runs = atoi(argv[++i]);
    else mode = argv[i];
  }
  char path[1024]; i64 n;
  snprintf(path, sizeof path, "%s/meta.txt", out); char *m = slurp(path, &n); if (!m) die("meta.txt");
  int maxreg = (int)meta(m, "max_reg_used");
  snprintf(path, sizeof path, "%s/ram.bin", out); g_ram = (u8 *)slurp(path, &g_ramsz); if (!g_ram) die("ram.bin");
  if (g_ramsz != meta(m, "ram")) die("ram size");
  snprintf(path, sizeof path, "%s/prog.txt", out); double t0 = now(); load_prog(path);

  sqlite3 *db; if (sqlite3_open(":memory:", &db)) die("open");
#ifdef SQLITE_ENABLE_STMT_SCANSTATUS
  sqlite3_db_config(db, SQLITE_DBCONFIG_STMT_SCANSTATUS, 1, 0);
#endif
  int det = SQLITE_UTF8 | SQLITE_DETERMINISTIC | SQLITE_INNOCUOUS;   /* qvm calls only deterministic user functions; see DESIGN.md */
  sqlite3_create_function(db, "get_byte", 2, det, 0, f_get_byte, 0, 0);
  sqlite3_create_function(db, "set_byte", 3, det, 0, f_set_byte, 0, 0);
  sqlite3_create_function(db, "vdbe_blob_read", 2, det, 0, f_blob_read, 0, 0);
  sqlite3_create_function(db, "vdbe_blob_write", 2, det, 0, f_blob_write, 0, 0);
  sqlite3_create_function(db, "vdbe_now_ms", 0, det, 0, f_now_ms, 0, 0);
  sqlite3_create_function(db, "vdbe_sleep_ms", 1, det, 0, f_sleep_ms, 0, 0);
  sqlite3_stmt *st = build(db, maxreg);
  fprintf(stderr, "doomsql: loaded in %.2f s\n", now() - t0);

  u8 *ram0 = malloc((size_t)g_ramsz); memcpy(ram0, g_ram, (size_t)g_ramsz);
  int qc = 0;
#ifdef WITH_QC
  if (strcmp(mode, "stock")) {
    if (!strcmp(mode, "qvm")) setenv("QC_VM_ONLY", "1", 1);
    sqlite3_exec(db, "BEGIN", 0, 0, 0);
    qc = sqlite3_qc(st);
    fprintf(stderr, "doomsql: sqlite3_qc = %d (%s)\n", qc, qc ? "compiled" : qc_why);
    if (qc && g_max < (1 << 30))   /* qvm hands rows out when the program ends: it can't stop after N frames */
      fprintf(stderr, "doomsql: warning: --frames %d is ignored by %s, which runs the whole program; compare whole-program runs\n", g_max, mode);
  }
#else
  if (strcmp(mode, "stock")) die("built without qc");
#endif
  for (g_run = 0; g_run < runs; g_run++) {
    memcpy(g_ram, ram0, (size_t)g_ramsz); g_nf = 0;
    double t1 = now(); int rc = 0; const char *path_used = "sqlite3_step";
#ifdef WITH_QC
    if (qc) {
      rc = sqlite3_qc_exec(st, qc_rows, 0);
      if (rc == SQLITE_OK) path_used = "sqlite3_qc_exec";
      else { fprintf(stderr, "doomsql: qc_exec fell back (%s / %s) after %d frames\n", qc_why, qv_why, g_nf); g_nf = 0; memcpy(g_ram, ram0, (size_t)g_ramsz); t1 = now(); }
    }
#endif
    if (!strcmp(path_used, "sqlite3_step")) {
      sqlite3_reset(st);
      while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
        frame(sqlite3_column_blob(st, 1), sqlite3_column_bytes(st, 1));
        if (g_nf >= g_max) break;
      }
      if (rc != SQLITE_ROW && rc != SQLITE_DONE) {
        Vdbe *v = (Vdbe *)st; int pc = v->pc >= 0 && v->pc < v->nOp ? v->pc : 0;
        fprintf(stderr, "doomsql: step: %s [%s] (rc %d, pc %d %s p1 %d p2 %d p3 %d)\n", sqlite3_errmsg(db), v->zErrMsg ? v->zErrMsg : "", rc, v->pc,
                sqlite3OpcodeName(v->aOp[pc].opcode), v->aOp[pc].p1, v->aOp[pc].p2, v->aOp[pc].p3);
        return 1;
      }
      sqlite3_reset(st);
    }
    double s = now() - t1; int shown = g_nf < g_max ? g_nf : g_max;
    fprintf(stderr, "doomsql: run %d: %s via %s: %d frames (%d rows) in %.2f s = %.2f frames/s\n", g_run + 1, mode, path_used, shown, g_nf, s, g_nf / s);
  }
  if (runs > 1) fprintf(stderr, "doomsql: %d later-run frames differ from run 1\n", g_bad);
#ifdef SQLITE_ENABLE_STMT_SCANSTATUS   /* build.sh opcount: the dynamic opcode mix of the interpreter runs (stderr) */
  { u64 cnt[256] = {0}, tot = 0; Vdbe *v = (Vdbe *)st;
    for (int i = 0; i < v->nOp; i++) { cnt[v->aOp[i].opcode] += v->aOp[i].nExec; tot += v->aOp[i].nExec; }
    for (int k = 0; k < 20; k++) {
      int b = 0; for (int o = 1; o < 256; o++) if (cnt[o] > cnt[b]) b = o;
      if (!cnt[b]) break;
      fprintf(stderr, "opmix %-12s %12llu %5.1f%%\n", sqlite3OpcodeName(b), (unsigned long long)cnt[b], 100.0 * cnt[b] / tot);
      cnt[b] = 0;
    }
    fprintf(stderr, "opmix total %llu ops\n", (unsigned long long)tot); }
#endif
  sqlite3_finalize(st); sqlite3_close(db);
  return g_bad != 0;
}
#endif
