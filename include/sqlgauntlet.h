/* sqlgauntlet.h: the backend ABI (version 1) and the driver library's entry points.
**
** A backend is a table of functions, sg_backend. An in-process engine fills it directly (adapters/sqlite.c over any
** SQLite-API library, adapters/turso.c over Turso's C ABI); a networked one fills it with a client (open = connect,
** step = fetch the next row of the result). The driver only ever calls through the table, so one driver measures
** every backend the same way.
**
** Two ways to use it:
**   - as a plug-in: build the adapter and its engine into a shared object that exports sqlgauntlet_backend_v1(); the
**     sqlgauntlet CLI loads it with --backend <file> (dlopen, RTLD_LOCAL, so several SQLite builds never clash).
**   - as a library: link libsqlgauntlet.a and call sg_main() with a table of your own (a project testing its engine
**     links its build statically, with no copy of this repository in its tree: see README "Using sqlgauntlet from
**     another project").
**
** Values cross the ABI as sg_value: SQLite's five storage classes, with the same numbers. Text and blob pointers stay
** valid until the next call on that statement (step, column on another index, reset, finalize). */
#ifndef SQLGAUNTLET_H
#define SQLGAUNTLET_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

#define SQLGAUNTLET_ABI 1

enum { SG_INTEGER = 1, SG_FLOAT = 2, SG_TEXT = 3, SG_BLOB = 4, SG_NULL = 5 };   /* (SQLITE_INTEGER ... SQLITE_NULL) */
enum { SG_OK = 0, SG_ROW = 100, SG_DONE = 101, SG_ERROR = 1 };

typedef struct sg_value {
  int type;
  int64_t i;          /* SG_INTEGER */
  double f;           /* SG_FLOAT */
  const void *p;      /* SG_TEXT (UTF-8, not necessarily NUL-terminated), SG_BLOB */
  size_t n;           /* bytes at p */
} sg_value;

/* per-execution numbers a backend may know better than the driver's clock (all optional: -1 = unknown) */
typedef struct sg_exec_stats {
  int served;          /* 1: the backend's fast path ran this execution (turbo: SQLite's VM never stepped), 0: the
                          reference path, -1: the backend has no such notion */
  double engine_ms;    /* time the engine itself spent (a server's own measure); the driver reports wall - engine as
                          protocol time for client adapters */
  int64_t engine_counters[4];   /* backend-defined (e.g. VM steps, pages read); named by counter_names */
} sg_exec_stats;

typedef struct sg_conn sg_conn;   /* opaque to the driver */
typedef struct sg_stmt sg_stmt;

typedef struct sg_backend {
  int abi;                                   /* SQLGAUNTLET_ABI */
  const char *name;                          /* short, e.g. "sqlite", "turso" */
  const char *dialect;                       /* "sqlite", "duckdb", "postgres", "mysql": picks the corpus variant */
  const char *(*version)(void);              /* engine version and build, one line */

  sg_conn *(*open)(const char *path, char *err, size_t errlen);
  void (*close)(sg_conn *);
  int (*exec)(sg_conn *, const char *sql, char *err, size_t errlen);   /* a script: schema, data, pragmas */
  sg_stmt *(*prepare)(sg_conn *, const char *sql, char *err, size_t errlen);   /* first statement of sql */
  int (*param_count)(sg_stmt *);
  int (*param_index)(sg_stmt *, const char *name);   /* "?3", ":name", "$name", "@name": 1-based; 0 = none */
  int (*bind)(sg_stmt *, int idx, const sg_value *);
  int (*step)(sg_stmt *);                    /* SG_ROW, SG_DONE or SG_ERROR */
  int (*column_count)(sg_stmt *);
  void (*column)(sg_stmt *, int i, sg_value *);
  int (*reset)(sg_stmt *);                   /* keeps the bindings */
  void (*finalize)(sg_stmt *);
  int64_t (*changes)(sg_conn *);             /* rows the last write changed */
  const char *(*errmsg)(sg_conn *);

  /* optional: NULL when the backend has none */
  void (*exec_stats)(sg_stmt *, sg_exec_stats *);   /* after an execution's last step, before reset */
  void (*exec_begin)(sg_stmt *);             /* before an execution's first step (e.g. zero a counter) */
  const char *counter_names;                 /* comma-separated names of engine_counters[] */
  const char *(*explain)(sg_conn *, const char *sql);   /* the plan as text (diagnosis only) */
} sg_backend;

typedef const sg_backend *(*sg_backend_fn)(void);
#define SQLGAUNTLET_BACKEND_SYMBOL "sqlgauntlet_backend_v1"

/* the driver: the CLI's main, callable with a backend table instead of --backend (argv as the CLI's, see sqlgauntlet.c) */
int sg_main(const sg_backend *b, int argc, char **argv);

#ifdef __cplusplus
}
#endif
#endif
