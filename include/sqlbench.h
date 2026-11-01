/* sqlbench.h: the backend ABI (version 1) and the driver library's entry points.
**
** A backend is a table of functions, sb_backend. An in-process engine fills it directly (adapters/sqlite.c over any
** SQLite-API library, adapters/turso.c over Turso's C ABI); a networked one fills it with a client (open = connect,
** step = fetch the next row of the result). The driver only ever calls through the table, so one driver measures
** every backend the same way.
**
** Two ways to use it:
**   - as a plug-in: build the adapter and its engine into a shared object that exports sqlbench_backend_v1(); the
**     sqlbench CLI loads it with --backend <file> (dlopen, RTLD_LOCAL, so several SQLite builds never clash).
**   - as a library: link libsqlbench.a and call sb_main() with a table of your own (a project testing its engine
**     links its build statically, with no copy of this repository in its tree: see README "Using sqlbench from
**     another project").
**
** Values cross the ABI as sb_value: SQLite's five storage classes, with the same numbers. Text and blob pointers stay
** valid until the next call on that statement (step, column on another index, reset, finalize). */
#ifndef SQLBENCH_H
#define SQLBENCH_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif

#define SQLBENCH_ABI 1

enum { SB_INTEGER = 1, SB_FLOAT = 2, SB_TEXT = 3, SB_BLOB = 4, SB_NULL = 5 };   /* (SQLITE_INTEGER ... SQLITE_NULL) */
enum { SB_OK = 0, SB_ROW = 100, SB_DONE = 101, SB_ERROR = 1 };

typedef struct sb_value {
  int type;
  int64_t i;          /* SB_INTEGER */
  double f;           /* SB_FLOAT */
  const void *p;      /* SB_TEXT (UTF-8, not necessarily NUL-terminated), SB_BLOB */
  size_t n;           /* bytes at p */
} sb_value;

/* per-execution numbers a backend may know better than the driver's clock (all optional: -1 = unknown) */
typedef struct sb_exec_stats {
  int served;          /* 1: the backend's fast path ran this execution (turbo: SQLite's VM never stepped), 0: the
                          reference path, -1: the backend has no such notion */
  double engine_ms;    /* time the engine itself spent (a server's own measure); the driver reports wall - engine as
                          protocol time for client adapters */
  int64_t engine_counters[4];   /* backend-defined (e.g. VM steps, pages read); named by counter_names */
} sb_exec_stats;

typedef struct sb_conn sb_conn;   /* opaque to the driver */
typedef struct sb_stmt sb_stmt;

typedef struct sb_backend {
  int abi;                                   /* SQLBENCH_ABI */
  const char *name;                          /* short, e.g. "sqlite", "turso" */
  const char *dialect;                       /* "sqlite", "duckdb", "postgres", "mysql": picks the corpus variant */
  const char *(*version)(void);              /* engine version and build, one line */

  sb_conn *(*open)(const char *path, char *err, size_t errlen);
  void (*close)(sb_conn *);
  int (*exec)(sb_conn *, const char *sql, char *err, size_t errlen);   /* a script: schema, data, pragmas */
  sb_stmt *(*prepare)(sb_conn *, const char *sql, char *err, size_t errlen);   /* first statement of sql */
  int (*param_count)(sb_stmt *);
  int (*param_index)(sb_stmt *, const char *name);   /* "?3", ":name", "$name", "@name": 1-based; 0 = none */
  int (*bind)(sb_stmt *, int idx, const sb_value *);
  int (*step)(sb_stmt *);                    /* SB_ROW, SB_DONE or SB_ERROR */
  int (*column_count)(sb_stmt *);
  void (*column)(sb_stmt *, int i, sb_value *);
  int (*reset)(sb_stmt *);                   /* keeps the bindings */
  void (*finalize)(sb_stmt *);
  int64_t (*changes)(sb_conn *);             /* rows the last write changed */
  const char *(*errmsg)(sb_conn *);

  /* optional: NULL when the backend has none */
  void (*exec_stats)(sb_stmt *, sb_exec_stats *);   /* after an execution's last step, before reset */
  void (*exec_begin)(sb_stmt *);             /* before an execution's first step (e.g. zero a counter) */
  const char *counter_names;                 /* comma-separated names of engine_counters[] */
  const char *(*explain)(sb_conn *, const char *sql);   /* the plan as text (diagnosis only) */
} sb_backend;

typedef const sb_backend *(*sb_backend_fn)(void);
#define SQLBENCH_BACKEND_SYMBOL "sqlbench_backend_v1"

/* the driver: the CLI's main, callable with a backend table instead of --backend (argv as the CLI's, see sqlbench.c) */
int sb_main(const sb_backend *b, int argc, char **argv);

#ifdef __cplusplus
}
#endif
#endif
