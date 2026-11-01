/* turso.c: the backend over Turso's C ABI (sdk-kit/turso.h; Turso, formerly Limbo, a Rust rewrite of SQLite's engine
** that reads and writes SQLite files). Synchronous I/O. Bindings are kept here and bound again after each reset, since
** turso.h doesn't promise that a reset keeps them (sqlite3_reset does: the driver relies on it). Turso has no notion
** of "served" (-1). Build: scripts/fetch-turso.sh build, then make build/backends/turso.so. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "turso.h"
#include "sqlgauntlet.h"

typedef struct { const turso_database_t *d; turso_connection_t *c; char err[512]; int64_t changes; } TC;
typedef struct { turso_statement_t *s; TC *c; int nb; sg_value *b; void **own; } TS;

static void seterr(TC *c, const char *e, char *out, size_t n) {
  snprintf(c->err, sizeof c->err, "%s", e ? e : "turso error");
  if (out) snprintf(out, n, "%s", c->err);
  if (e) turso_str_deinit(e);
}
static sg_conn *t_open(const char *path, char *err, size_t n) {
  TC *c = calloc(1, sizeof *c); const char *e = 0;
  turso_database_config_t cfg; memset(&cfg, 0, sizeof cfg); cfg.path = path;
  if (turso_database_new(&cfg, &c->d, &e) != TURSO_OK || turso_database_open(c->d, &e) != TURSO_OK
      || turso_database_connect(c->d, &c->c, &e) != TURSO_OK) { seterr(c, e, err, n); return 0; }
  return (sg_conn *)c;
}
static void t_close(sg_conn *x) {
  TC *c = (TC *)x; turso_connection_close(c->c, 0); turso_connection_deinit(c->c); turso_database_deinit(c->d); free(c);
}
static int t_exec(sg_conn *x, const char *sql, char *err, size_t n) {
  TC *c = (TC *)x; const char *p = sql;
  while (*p) {
    turso_statement_t *s = 0; size_t tail = 0; const char *e = 0;
    if (turso_connection_prepare_first(c->c, p, &s, &tail, &e) != TURSO_OK) { seterr(c, e, err, n); return 1; }
    if (!s) break;
    for (;;) {
      int rc = turso_statement_step(s, &e);
      if (rc == TURSO_ROW) continue;
      if (rc == TURSO_DONE) break;
      seterr(c, e, err, n); turso_statement_finalize(s, 0); turso_statement_deinit(s); return 1;
    }
    turso_statement_finalize(s, 0); turso_statement_deinit(s);
    if (!tail) break;
    p += tail;
  }
  return 0;
}
static sg_stmt *t_prepare(sg_conn *x, const char *sql, char *err, size_t n) {
  TC *c = (TC *)x; turso_statement_t *s = 0; const char *e = 0; size_t tail = 0;
  if (turso_connection_prepare_first(c->c, sql, &s, &tail, &e) != TURSO_OK) { seterr(c, e, err, n); return 0; }
  if (!s) { snprintf(err, n, "no statement"); return 0; }
  TS *t = calloc(1, sizeof *t); t->s = s; t->c = c;
  t->nb = (int)turso_statement_parameters_count(s); if (t->nb < 0) t->nb = 0;
  t->b = calloc((size_t)t->nb + 1, sizeof *t->b); t->own = calloc((size_t)t->nb + 1, sizeof *t->own);
  return (sg_stmt *)t;
}
static int t_param_count(sg_stmt *x) { return ((TS *)x)->nb; }
static int t_param_index(sg_stmt *x, const char *name) {
  TS *t = (TS *)x; int64_t k = turso_statement_named_position(t->s, name);
  if (k > 0) return (int)k;
  for (int64_t i = 1; i <= t->nb; i++) {   /* (named_position may want the name without its prefix, or an exact one) */
    const char *nm = turso_statement_parameter_name(t->s, i);
    int hit = nm && !strcmp(nm, name); if (nm) turso_str_deinit(nm);
    if (hit) return (int)i;
  }
  return 0;
}
static int bind1(TS *t, int i, const sg_value *v) {
  turso_statement_t *s = t->s; int rc;
  switch (v->type) {
    case SG_INTEGER: rc = turso_statement_bind_positional_int(s, (size_t)i, v->i); break;
    case SG_FLOAT: rc = turso_statement_bind_positional_double(s, (size_t)i, v->f); break;
    case SG_TEXT: rc = turso_statement_bind_positional_text(s, (size_t)i, v->p, v->n); break;
    case SG_BLOB: rc = turso_statement_bind_positional_blob(s, (size_t)i, v->p, v->n); break;
    default: rc = turso_statement_bind_positional_null(s, (size_t)i);
  }
  return rc == TURSO_OK ? SG_OK : SG_ERROR;
}
static int t_bind(sg_stmt *x, int i, const sg_value *v) {
  TS *t = (TS *)x; if (i < 1 || i > t->nb) return SG_ERROR;
  free(t->own[i]); t->own[i] = 0; t->b[i] = *v;
  if ((v->type == SG_TEXT || v->type == SG_BLOB) && v->n) { t->own[i] = malloc(v->n); memcpy(t->own[i], v->p, v->n); t->b[i].p = t->own[i]; }
  return bind1(t, i, &t->b[i]);
}
static int t_step(sg_stmt *x) {
  TS *t = (TS *)x; const char *e = 0; int rc = turso_statement_step(t->s, &e);
  if (rc == TURSO_ROW) return SG_ROW;
  if (rc == TURSO_DONE) { t->c->changes = turso_statement_n_change(t->s); return SG_DONE; }
  seterr(t->c, e, 0, 0); return SG_ERROR;
}
static int t_column_count(sg_stmt *x) { return (int)turso_statement_column_count(((TS *)x)->s); }
static void t_column(sg_stmt *x, int i, sg_value *v) {
  turso_statement_t *s = ((TS *)x)->s; memset(v, 0, sizeof *v);
  switch (turso_statement_row_value_kind(s, (size_t)i)) {
    case TURSO_TYPE_INTEGER: v->type = SG_INTEGER; v->i = turso_statement_row_value_int(s, (size_t)i); break;
    case TURSO_TYPE_REAL: v->type = SG_FLOAT; v->f = turso_statement_row_value_double(s, (size_t)i); break;
    case TURSO_TYPE_TEXT: v->type = SG_TEXT; v->p = turso_statement_row_value_bytes_ptr(s, (size_t)i); v->n = (size_t)turso_statement_row_value_bytes_count(s, (size_t)i); break;
    case TURSO_TYPE_BLOB: v->type = SG_BLOB; v->p = turso_statement_row_value_bytes_ptr(s, (size_t)i); v->n = (size_t)turso_statement_row_value_bytes_count(s, (size_t)i); break;
    default: v->type = SG_NULL;
  }
}
static int t_reset(sg_stmt *x) {
  TS *t = (TS *)x; const char *e = 0;
  if (turso_statement_reset(t->s, &e) != TURSO_OK) { seterr(t->c, e, 0, 0); return SG_ERROR; }
  for (int i = 1; i <= t->nb; i++) if (t->b[i].type) bind1(t, i, &t->b[i]);
  return SG_OK;
}
static void t_finalize(sg_stmt *x) {
  TS *t = (TS *)x; turso_statement_finalize(t->s, 0); turso_statement_deinit(t->s);
  for (int i = 0; i <= t->nb; i++) free(t->own[i]);
  free(t->own); free(t->b); free(t);
}
static int64_t t_changes(sg_conn *x) { return ((TC *)x)->changes; }
static const char *t_errmsg(sg_conn *x) { return ((TC *)x)->err; }
static const char *t_version(void) { static char b[64]; snprintf(b, sizeof b, "Turso %s", turso_version()); return b; }

static const sg_backend backend = {
  SQLGAUNTLET_ABI, "turso", "sqlite", t_version,
  t_open, t_close, t_exec, t_prepare, t_param_count, t_param_index, t_bind, t_step, t_column_count, t_column,
  t_reset, t_finalize, t_changes, t_errmsg,
  0, 0, 0, 0,
};
__attribute__((visibility("default"))) const sg_backend *sqlgauntlet_backend_v1(void) { return &backend; }
