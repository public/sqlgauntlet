/* sqlite.c: the backend over SQLite's C API, for any library that provides it: stock SQLite, cursed-sqlite-turbo,
** libSQL. Compile it with that library's sqlite3.h and link its sqlite3.o (adapters/build.sh). It adds regexp() (SQLite's
** ext/misc/regexp.c, as the sqlite3 CLI the corpus was recorded with) unless the library has its own. served: the
** execution never stepped SQLite's VM (SQLITE_STMTSTATUS_VM_STEP stayed 0), which is what an engine serving
** statements inside sqlite3_step (turbo) looks like; stock SQLite always steps it, so stock reports 0. */
#include <string.h>
#include <stdio.h>
#include "sqlite3.h"
#include "sqlgauntlet.h"

#ifndef SG_NO_REGEXP
#define SQLITE_EXTENSION_INIT1
#define SQLITE_EXTENSION_INIT2(x)
#include "regexp.c"
#endif
#ifndef SG_VERSION_NOTE
#define SG_VERSION_NOTE ""
#endif
#ifndef SG_NAME
#define SG_NAME "sqlite"
#endif

static sg_conn *a_open(const char *path, char *err, size_t n) {
  sqlite3 *db;
  if (sqlite3_open(path, &db) != SQLITE_OK) {
    snprintf(err, n, "%s", db ? sqlite3_errmsg(db) : "out of memory");
    sqlite3_close(db);
    return 0;
  }
#ifndef SG_NO_REGEXP
  sqlite3_regexp_init(db, 0, 0);
#endif
  return (sg_conn *)db;
}
static void a_close(sg_conn *c) { sqlite3_close((sqlite3 *)c); }
static int a_exec(sg_conn *c, const char *sql, char *err, size_t n) {
  char *e = 0;
  int rc = sqlite3_exec((sqlite3 *)c, sql, 0, 0, &e);
  if (rc != SQLITE_OK) {
    snprintf(err, n, "%s", e ? e : sqlite3_errmsg((sqlite3 *)c));
    sqlite3_free(e);
    return 1;
  }
  return 0;
}
static sg_stmt *a_prepare(sg_conn *c, const char *sql, char *err, size_t n) {
  sqlite3_stmt *st = 0;
  if (sqlite3_prepare_v2((sqlite3 *)c, sql, -1, &st, 0) != SQLITE_OK) {
    snprintf(err, n, "%s", sqlite3_errmsg((sqlite3 *)c));
    sqlite3_finalize(st);
    return 0;
  }
  if (!st) {
    snprintf(err, n, "no statement");
    return 0;
  }
  return (sg_stmt *)st;
}
static int a_param_count(sg_stmt *s) { return sqlite3_bind_parameter_count((sqlite3_stmt *)s); }
static int a_param_index(sg_stmt *s, const char *name) { return sqlite3_bind_parameter_index((sqlite3_stmt *)s, name); }
static int a_bind(sg_stmt *s, int i, const sg_value *v) {
  sqlite3_stmt *st = (sqlite3_stmt *)s;
  switch (v->type) {
    case SG_INTEGER: return sqlite3_bind_int64(st, i, v->i);
    case SG_FLOAT: return sqlite3_bind_double(st, i, v->f);
    case SG_TEXT: return sqlite3_bind_text64(st, i, v->p, v->n, SQLITE_TRANSIENT, SQLITE_UTF8);
    case SG_BLOB: return sqlite3_bind_blob64(st, i, v->p, v->n, SQLITE_TRANSIENT);
    default: return sqlite3_bind_null(st, i);
  }
}
static int a_step(sg_stmt *s) {
  int rc = sqlite3_step((sqlite3_stmt *)s);
  return rc == SQLITE_ROW ? SG_ROW : rc == SQLITE_DONE ? SG_DONE : SG_ERROR;
}
static int a_column_count(sg_stmt *s) { return sqlite3_column_count((sqlite3_stmt *)s); }
static void a_column(sg_stmt *s, int i, sg_value *v) {
  sqlite3_stmt *st = (sqlite3_stmt *)s;
  memset(v, 0, sizeof *v);
  v->type = sqlite3_column_type(st, i);
  switch (v->type) {
    case SQLITE_INTEGER: v->i = sqlite3_column_int64(st, i); break;
    case SQLITE_FLOAT: v->f = sqlite3_column_double(st, i); break;
    case SQLITE_TEXT:
      v->p = sqlite3_column_text(st, i);
      v->n = (size_t)sqlite3_column_bytes(st, i);
      break;
    case SQLITE_BLOB:
      v->p = sqlite3_column_blob(st, i);
      v->n = (size_t)sqlite3_column_bytes(st, i);
      break;
  }
}
static int a_reset(sg_stmt *s) { return sqlite3_reset((sqlite3_stmt *)s); }
static void a_finalize(sg_stmt *s) { sqlite3_finalize((sqlite3_stmt *)s); }
static int64_t a_changes(sg_conn *c) { return sqlite3_changes((sqlite3 *)c); }
static const char *a_errmsg(sg_conn *c) { return sqlite3_errmsg((sqlite3 *)c); }
static void a_exec_begin(sg_stmt *s) { sqlite3_stmt_status((sqlite3_stmt *)s, SQLITE_STMTSTATUS_VM_STEP, 1); }
static void a_exec_stats(sg_stmt *s, sg_exec_stats *e) {
  int vm = sqlite3_stmt_status((sqlite3_stmt *)s, SQLITE_STMTSTATUS_VM_STEP, 0);
  e->served = vm == 0;
  e->engine_counters[0] = vm;
}
static const char *a_version(void) {
  static char b[160];
#ifdef LIBSQL_VERSION
  snprintf(b, sizeof b, "libSQL %s (SQLite %s %.20s)", LIBSQL_VERSION, sqlite3_libversion(), sqlite3_sourceid());
#else
  snprintf(b, sizeof b, "SQLite %s %.20s%s", sqlite3_libversion(), sqlite3_sourceid(), SG_VERSION_NOTE);
#endif
  return b;
}
static const sg_backend backend = {
  SQLGAUNTLET_ABI, SG_NAME,        "sqlite", a_version, a_open,     a_close,   a_exec,   a_prepare,    a_param_count, a_param_index, a_bind,
  a_step,          a_column_count, a_column, a_reset,   a_finalize, a_changes, a_errmsg, a_exec_stats, a_exec_begin,  "vm_steps",    0,
};
__attribute__((visibility("default"))) const sg_backend *sqlgauntlet_backend_v1(void) { return &backend; }
