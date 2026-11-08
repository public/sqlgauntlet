/* backends/duckdb/adapter.c: the backend over DuckDB's C API (duckdb.h), an in-process engine in its own dialect.
** - Databases: a DuckDB file; run.py builds it from the SQLite reference's export (export.py, dialect "duckdb").
** - Statements: duckdb_prepare, parameters $1..$N (the dialect layer numbers them), results materialised by the first
**   step (duckdb_execute_prepared) and read row by row.
** - Values: BOOLEAN and the integer types up to BIGINT (and UINTEGER) as integers; UBIGINT, HUGEINT, UHUGEINT and DECIMAL
**   with scale 0 as an integer when the value fits int64, else a float; FLOAT, DOUBLE and DECIMAL as floats; BLOB as a
**   blob; everything else (VARCHAR, dates, times, intervals, UUID, lists, structs, JSON, ...) as DuckDB's text form.
** - changes: duckdb_rows_changed of the last execution.
** - Footprint: one thread, a 512 MB memory limit and at most 2 GB spilled to disk unless SG_DUCKDB_THREADS,
**   SG_DUCKDB_MEMORY, SG_DUCKDB_TEMP_MAX say otherwise (DuckDB spills past the memory limit; a query that needs more
**   scratch space than the cap fails; one thread also keeps an unordered result in one order from run to run). */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "duckdb.h"
#include "sqlgauntlet.h"

typedef struct {
  duckdb_database db;
  duckdb_connection con;
  char err[512];
  int64_t changes;
} DC;
typedef struct {
  DC *c;
  duckdb_prepared_statement ps;
  duckdb_result res;
  int have, nc;
  idx_t row, nrows;
  void *cell; /* the current column's text or blob (DuckDB's allocation) */
} DS;

static void seterr(DC *c, const char *e, char *out, size_t n) {
  snprintf(c->err, sizeof c->err, "%s", e && *e ? e : "duckdb error");
  for (char *p = c->err; *p; p++)
    if (*p == '\n') *p = ' ';
  if (out) snprintf(out, n, "%s", c->err);
}
static sg_conn *d_open(const char *path, char *err, size_t n) {
  DC *c = calloc(1, sizeof *c);
  char *e = 0;
  duckdb_config cfg; /* (a fixed, small footprint: by default DuckDB takes 80% of RAM and every core) */
  duckdb_create_config(&cfg);
  const char *th = getenv("SG_DUCKDB_THREADS"), *mem = getenv("SG_DUCKDB_MEMORY");
  duckdb_set_config(cfg, "threads", th && *th ? th : "1");
  duckdb_set_config(cfg, "memory_limit", mem && *mem ? mem : "512MB");
  const char *tmp = getenv("SG_DUCKDB_TEMP_MAX"); /* (a query that would spill more fails instead of filling the disk) */
  duckdb_set_config(cfg, "max_temp_directory_size", tmp && *tmp ? tmp : "2GB");
  duckdb_set_config(cfg, "integer_division", "true"); /* (integer / integer is integer division, as in SQLite: sqlglot
                                                         writes SQLite's / unchanged, and DuckDB's / is otherwise real) */
  int bad = duckdb_open_ext(path, &c->db, cfg, &e) != DuckDBSuccess;
  duckdb_destroy_config(&cfg);
  if (bad) {
    snprintf(err, n, "open %s: %s", path, e ? e : "?");
    duckdb_free(e);
    free(c);
    return 0;
  }
  if (duckdb_connect(c->db, &c->con) != DuckDBSuccess) {
    snprintf(err, n, "connect %s", path);
    duckdb_close(&c->db);
    free(c);
    return 0;
  }
  return (sg_conn *)c;
}
static void d_close(sg_conn *x) {
  DC *c = (DC *)x;
  duckdb_disconnect(&c->con);
  duckdb_close(&c->db);
  free(c);
}
static int d_exec(sg_conn *x, const char *sql, char *err, size_t n) { /* a script: duckdb_query runs every statement */
  DC *c = (DC *)x;
  duckdb_result r;
  int bad = duckdb_query(c->con, sql, &r) != DuckDBSuccess;
  if (bad) seterr(c, duckdb_result_error(&r), err, n);
  duckdb_destroy_result(&r);
  return bad;
}
static sg_stmt *d_prepare(sg_conn *x, const char *sql, char *err, size_t n) {
  DC *c = (DC *)x;
  DS *s = calloc(1, sizeof *s);
  s->c = c;
  if (duckdb_prepare(c->con, sql, &s->ps) != DuckDBSuccess) {
    seterr(c, duckdb_prepare_error(s->ps), err, n);
    duckdb_destroy_prepare(&s->ps);
    free(s);
    return 0;
  }
  return (sg_stmt *)s;
}
static int d_param_count(sg_stmt *x) { return (int)duckdb_nparams(((DS *)x)->ps); }
static int d_param_index(sg_stmt *x, const char *name) { /* "$N" and "?N" (the dialect layer numbers them), else by name */
  DS *s = (DS *)x;
  int np = (int)duckdb_nparams(s->ps);
  if (name[0] == '$' || name[0] == '?') {
    char *e;
    long k = strtol(name + 1, &e, 10);
    if (!*e && e > name + 1) return k >= 1 && k <= np ? (int)k : 0;
  }
  idx_t i = 0;
  return duckdb_bind_parameter_index(s->ps, &i, name + 1) == DuckDBSuccess ? (int)i : 0;
}
static int d_bind(sg_stmt *x, int i, const sg_value *v) {
  DS *s = (DS *)x;
  duckdb_state r;
  switch (v->type) {
    case SG_INTEGER: r = duckdb_bind_int64(s->ps, (idx_t)i, v->i); break;
    case SG_FLOAT: r = duckdb_bind_double(s->ps, (idx_t)i, v->f); break;
    case SG_TEXT: r = duckdb_bind_varchar_length(s->ps, (idx_t)i, v->p, (idx_t)v->n); break;
    case SG_BLOB: r = duckdb_bind_blob(s->ps, (idx_t)i, v->p, (idx_t)v->n); break;
    default: r = duckdb_bind_null(s->ps, (idx_t)i);
  }
  return r == DuckDBSuccess ? SG_OK : SG_ERROR;
}
static int d_step(sg_stmt *x) {
  DS *s = (DS *)x;
  if (!s->have) {
    s->have = 1;
    if (duckdb_execute_prepared(s->ps, &s->res) != DuckDBSuccess) {
      seterr(s->c, duckdb_result_error(&s->res), 0, 0);
      s->nrows = 0;
      s->row = 0;
      s->have = 2; /* (failed) */
      return SG_ERROR;
    }
    s->nc = (int)duckdb_column_count(&s->res);
    s->nrows = duckdb_row_count(&s->res);
    s->row = (idx_t)-1;
    duckdb_statement_type t = duckdb_prepared_statement_type(s->ps); /* (the result says EXECUTE for a prepared one) */
    int write = t == DUCKDB_STATEMENT_TYPE_INSERT || t == DUCKDB_STATEMENT_TYPE_UPDATE || t == DUCKDB_STATEMENT_TYPE_DELETE;
    s->c->changes = write ? (int64_t)duckdb_rows_changed(&s->res) : 0;
    const char *c0 = s->nc == 1 ? duckdb_column_name(&s->res, 0) : 0;
    if (write && c0 && !strcmp(c0, "Count") && duckdb_column_type(&s->res, 0) == DUCKDB_TYPE_BIGINT) {
      s->nrows = 0; /* (a write without RETURNING: DuckDB's one row holding the count is changes, not a result) */
      s->nc = 0;
    }
  }
  if (s->have == 2) return SG_ERROR;
  return ++s->row < s->nrows ? SG_ROW : SG_DONE;
}
static int d_column_count(sg_stmt *x) {
  DS *s = (DS *)x;
  return s->have == 1 ? s->nc : (int)duckdb_prepared_statement_column_count(s->ps);
}
static int as_int64(const char *t, int64_t *out) { /* a decimal integer's text that fits int64 */
  char *e;
  errno = 0;
  long long v = strtoll(t, &e, 10);
  if (errno || *e || e == t) return 0;
  *out = v;
  return 1;
}
static void d_column(sg_stmt *x, int i, sg_value *v) {
  DS *s = (DS *)x;
  memset(v, 0, sizeof *v);
  if (s->cell) {
    duckdb_free(s->cell);
    s->cell = 0;
  }
  if (s->have != 1 || s->row >= s->nrows || duckdb_value_is_null(&s->res, (idx_t)i, s->row)) {
    v->type = SG_NULL;
    return;
  }
  duckdb_type t = duckdb_column_type(&s->res, (idx_t)i);
  switch (t) {
    case DUCKDB_TYPE_BOOLEAN:
    case DUCKDB_TYPE_TINYINT:
    case DUCKDB_TYPE_SMALLINT:
    case DUCKDB_TYPE_INTEGER:
    case DUCKDB_TYPE_BIGINT:
    case DUCKDB_TYPE_UTINYINT:
    case DUCKDB_TYPE_USMALLINT:
    case DUCKDB_TYPE_UINTEGER:
      v->type = SG_INTEGER;
      v->i = duckdb_value_int64(&s->res, (idx_t)i, s->row);
      return;
    case DUCKDB_TYPE_FLOAT:
    case DUCKDB_TYPE_DOUBLE:
      v->type = SG_FLOAT;
      v->f = duckdb_value_double(&s->res, (idx_t)i, s->row);
      return;
    case DUCKDB_TYPE_BLOB: {
      duckdb_blob b = duckdb_value_blob(&s->res, (idx_t)i, s->row);
      s->cell = b.data;
      v->type = SG_BLOB;
      v->p = b.data;
      v->n = (size_t)b.size;
      return;
    }
    default: break;
  }
  char *txt = duckdb_value_varchar(&s->res, (idx_t)i, s->row);
  int numeric = t == DUCKDB_TYPE_UBIGINT || t == DUCKDB_TYPE_HUGEINT || t == DUCKDB_TYPE_UHUGEINT || t == DUCKDB_TYPE_DECIMAL;
  if (numeric && txt) { /* (an integer that fits, else a float) */
    if (as_int64(txt, &v->i)) v->type = SG_INTEGER;
    else {
      v->type = SG_FLOAT;
      v->f = strtod(txt, 0);
    }
    duckdb_free(txt);
    return;
  }
  s->cell = txt;
  v->type = SG_TEXT;
  v->p = txt ? txt : "";
  v->n = txt ? strlen(txt) : 0;
}
static int d_reset(sg_stmt *x) {
  DS *s = (DS *)x;
  if (s->cell) {
    duckdb_free(s->cell);
    s->cell = 0;
  }
  if (s->have) duckdb_destroy_result(&s->res);
  s->have = 0;
  return SG_OK;
}
static void d_finalize(sg_stmt *x) {
  DS *s = (DS *)x;
  d_reset(x);
  duckdb_destroy_prepare(&s->ps);
  free(s);
}
static int64_t d_changes(sg_conn *x) { return ((DC *)x)->changes; }
static const char *d_errmsg(sg_conn *x) { return ((DC *)x)->err; }
static const char *d_version(void) {
  static char b[96];
  snprintf(b, sizeof b, "DuckDB %s", duckdb_library_version());
  return b;
}

static const sg_backend backend = {
  SQLGAUNTLET_ABI,
  "duckdb",
  "duckdb",
  d_version,
  d_open,
  d_close,
  d_exec,
  d_prepare,
  d_param_count,
  d_param_index,
  d_bind,
  d_step,
  d_column_count,
  d_column,
  d_reset,
  d_finalize,
  d_changes,
  d_errmsg,
  0,
  0,
  0,
  0,
};
__attribute__((visibility("default"))) const sg_backend *sqlgauntlet_backend_v1(void) { return &backend; }
