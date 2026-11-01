# sqlgauntlet: the driver (build/libsqlgauntlet.a, the build/sqlgauntlet CLI) and the backends, one shared object each
# in build/backends/<name>.so beside <name>.json (its arm defaults: environment) and <name>.so.cflags (how it was built).
#   make                      the CLI and the library
#   make backends             every backend whose sources need no extra toolchain: sqlite, libsql
#   make sqlite turso ...     one backend (backends/<name>/backend.mk)
#   make sqlite-api NAME=<n> SRC=<dir with sqlite3.c, sqlite3.h> [EXTRA_CFLAGS=...] [ENV="K=V ..."]
#                             any library with SQLite's C API, as backend <n> (a fork, a patched build)
#   make callgrind            the CLI built for valgrind's callgrind (deterministic instruction counts)
#   make test, make lint
CC ?= cc
ARCH := $(if $(filter x86_64,$(shell uname -m)),-march=native,-mcpu=native)
CFLAGS ?= -O2 $(ARCH)
B := build
SO := -shared -fPIC -fvisibility=hidden
UNAME := $(shell uname)
LDL := $(if $(filter Linux,$(UNAME)),-ldl,)

all: $(B)/sqlgauntlet $(B)/libsqlgauntlet.a
backends: sqlite libsql
.PHONY: all backends clean test test-postgres lint

$(B)/driver.o: src/driver.c include/sqlgauntlet.h
	@mkdir -p $(B); $(CC) $(CFLAGS) -fPIC -Iinclude -c $< -o $@
$(B)/libsqlgauntlet.a: $(B)/driver.o
	ar rcs $@ $^
$(B)/sqlgauntlet: src/main.c $(B)/libsqlgauntlet.a
	$(CC) $(CFLAGS) -Iinclude $^ -o $@ $(LDL) -lm

# make callgrind: the CLI again with -DSG_CALLGRIND (Linux, valgrind's headers): sqlgauntlet-run --callgrind uses it
callgrind: $(B)/callgrind/sqlgauntlet
.PHONY: callgrind
$(B)/callgrind/sqlgauntlet: src/main.c src/driver.c include/sqlgauntlet.h
	@mkdir -p $(B)/callgrind; $(CC) -O2 -g -DSG_CALLGRIND -Iinclude src/main.c src/driver.c -o $@ $(LDL) -lm

# backend_json <name> <dialect> <env "K=V ...">: build/backends/<name>.json, the arm defaults python/sqlgauntlet/run.py reads
backend_json = python3 -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "dialect": sys.argv[2], \
  "env": dict(kv.split("=", 1) for kv in sys.argv[3:])}))' '$(1)' '$(2)' $(3) > $(B)/backends/$(1).json

include backends/sqlite/backend.mk   # (first: the others build on its SQLite-API rules)
include $(filter-out backends/sqlite/backend.mk,$(wildcard backends/*/backend.mk))

# make test: tests/smoke.py over a few vendored apps (TEST_APPS) with two SQLite-API backends, tests/workloads.py over
# TPC-H's path on generated rows; make lint: CI's lint job
test: all sqlite libsql
	python3 tests/smoke.py
	python3 tests/workloads.py
	python3 tests/regressions.py
# make test-postgres: tests/postgres.py (a PostgreSQL server at PG_URL, and sqlglot)
test-postgres: all sqlite postgres
	python3 tests/postgres.py
lint:
	uvx pre-commit run --all-files

clean:
	rm -rf $(B)
