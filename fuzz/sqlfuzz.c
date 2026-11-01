/* fuzz/sqlfuzz.c: a libFuzzer target over any backend's table (include/sqlgauntlet.h): the input is SQL text, run as a
** script (B->exec) on a fresh in-memory database that already holds a small schema with rows of awkward values, then
** its last statement once more through prepare and step, so both paths see it. Built by tools/fuzz-libfuzzer.sh with
** the backend's adapter and engine compiled in, under -fsanitize=fuzzer,address,undefined: a crash, a sanitizer report
** or a timeout is a finding (libFuzzer writes the input as crash-<sha1>; tools/fuzz-libfuzzer.sh keeps them).
** Errors from SQL are not findings: the input is mostly invalid SQL. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "sqlgauntlet.h"

const sg_backend *sqlgauntlet_backend_v1(void);
static const char *SETUP = "CREATE TABLE t0(c0 INTEGER PRIMARY KEY, c1 TEXT, c2 REAL, c3 BLOB, c4);"
                           "CREATE TABLE t1(c0 INTEGER, c1 TEXT COLLATE NOCASE, c2 NUMERIC, PRIMARY KEY(c0, c1)) WITHOUT ROWID;"
                           "CREATE INDEX t0_i ON t0(c1, c2 DESC) WHERE c4 IS NOT NULL; CREATE INDEX t1_e ON t1(lower(c1));"
                           "INSERT INTO t0 VALUES (1, 'a', 0.5, x'00ff', NULL), (2, '', -0.0, NULL, 'x'), (3, '10', 1e308, x'', 9223372036854775807),"
                           " (4, NULL, -1.5, 'b', -9223372036854775808), (5, 'é', 5e-324, x'c3', '1e3');"
                           "INSERT INTO t1 VALUES (1, 'A', 1), (1, 'b', NULL), (2, 'a', '2'), (-1, '', 1e15);";

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  static const sg_backend *B;
  if (!B) B = sqlgauntlet_backend_v1();
  if (size > 1 << 16) return 0;
  char *sql = malloc(size + 1), err[256];
  memcpy(sql, data, size);
  sql[size] = 0;
  sg_conn *c = B->open(":memory:", err, sizeof err);
  if (!c) {
    free(sql);
    return 0;
  }
  B->exec(c, SETUP, err, sizeof err);
  B->exec(c, sql, err, sizeof err);
  char *last = strrchr(sql, ';');
  while (last && last > sql && !last[1]) { /* (the last non-empty statement) */
    *last = 0;
    last = strrchr(sql, ';');
  }
  sg_stmt *s = B->prepare(c, last ? last + 1 : sql, err, sizeof err);
  if (s) {
    sg_value v;
    for (int rows = 0; rows < 10000 && B->step(s) == SG_ROW; rows++)
      for (int i = 0; i < B->column_count(s); i++) B->column(s, i, &v);
    B->finalize(s);
  }
  B->close(c);
  free(sql);
  return 0;
}
