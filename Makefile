# sqlgauntlet: the driver (libsqlgauntlet.a, the sqlgauntlet CLI) and the backends, one shared object each in build/backends.
#   make                      the CLI, the library and the stock backend
#   make backends             stock, turbo (TURBO=<cursed-sqlite-turbo checkout>) and libsql
#   make build/backends/<b>.so
# One set of compile options for every SQLite-family backend (cursed-sqlite-turbo's bench/lib.sh ones, with what the
# corpus needs: double-quoted strings, FTS4/FTS5, percentile()), so only the engine differs between them.
CC ?= cc
ARCH := $(if $(filter x86_64,$(shell uname -m)),-march=native,-mcpu=native)
CFLAGS ?= -O2 $(ARCH)
SQLOPTS := -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DEFAULT_WAL_SYNCHRONOUS=1 -DSQLITE_DQS=3 \
  -DSQLITE_LIKE_DOESNT_MATCH_BLOBS -DSQLITE_USE_ALLOCA -DSQLITE_OMIT_LOAD_EXTENSION -DSQLITE_OMIT_DEPRECATED \
  -DSQLITE_OMIT_SHARED_CACHE -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_PERCENTILE
TURBO ?= $(HOME)/Documents/my-code/cursed-sqlite-jit
B := build
SO := -shared -fPIC -fvisibility=hidden
UNAME := $(shell uname)
LDL := $(if $(filter Linux,$(UNAME)),-ldl,)

all: $(B)/sqlgauntlet $(B)/libsqlgauntlet.a $(B)/backends/stock.so
backends: $(B)/backends/stock.so $(B)/backends/turbo.so $(B)/backends/libsql.so
.PHONY: all backends clean

$(B)/driver.o: src/driver.c include/sqlgauntlet.h
	@mkdir -p $(B); $(CC) $(CFLAGS) -fPIC -Iinclude -c $< -o $@
$(B)/libsqlgauntlet.a: $(B)/driver.o
	ar rcs $@ $^
$(B)/sqlgauntlet: src/main.c $(B)/libsqlgauntlet.a
	$(CC) $(CFLAGS) -Iinclude $^ -o $@ $(LDL) -lm

third_party/sqlite/sqlite3.c:
	sh scripts/fetch-sqlite.sh
third_party/libsql/sqlite3.c:
	sh scripts/fetch-libsql.sh

# a backend: adapters/sqlite.c and one amalgamation in one shared object (only sqlgauntlet_backend_v1 exported)
define sqlite_backend
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SQLOPTS) $(SO) -w -Iinclude -Iadapters -I$(2) $(3) adapters/sqlite.c $(2)/sqlite3.c -o $@ -lm
	echo "$(CC) $(CFLAGS) $(SQLOPTS) $(3)" > $@.cflags
endef
$(B)/backends/stock.so: adapters/sqlite.c adapters/regexp.c include/sqlgauntlet.h third_party/sqlite/sqlite3.c
	$(call sqlite_backend,stock,third_party/sqlite,-DSG_NAME='"stock"')
$(B)/backends/libsql.so: adapters/sqlite.c adapters/regexp.c include/sqlgauntlet.h third_party/libsql/sqlite3.c
	$(call sqlite_backend,libsql,third_party/libsql,-DSG_NAME='"libsql"')

# turbo: cursed-sqlite-turbo's native build (qc/patch.py over the same SQLite 3.54.1, qc.c appended), serving inside
# sqlite3_step when the process runs with QC_SERVE=1 (python/sqlgauntlet/arms.py sets it per arm)
$(B)/turbo/sqlite3.c: third_party/sqlite/sqlite3.c $(wildcard $(TURBO)/qc/*)
	@mkdir -p $(B)/turbo
	python3 $(TURBO)/qc/patch.py third_party/sqlite/sqlite3.c $@.tmp
	python3 $(TURBO)/qc/gen_decoders.py $(B)/turbo/qc_dec.h
	printf '\n#include "qc.c"   /* cursed-sqlite-turbo */\n' >> $@.tmp; mv $@.tmp $@
	cp third_party/sqlite/sqlite3.h $(B)/turbo/
	git -C $(TURBO) rev-parse HEAD > $(B)/turbo/COMMIT; git -C $(TURBO) status --porcelain qc | head -c 2000 > $(B)/turbo/DIRTY
$(B)/backends/turbo.so: adapters/sqlite.c adapters/regexp.c include/sqlgauntlet.h $(B)/turbo/sqlite3.c
	$(call sqlite_backend,turbo,$(B)/turbo,-I$(TURBO)/qc -DSG_NAME='"turbo"' -DSG_VERSION_NOTE='" + turbo $(shell cut -c1-10 $(B)/turbo/COMMIT 2>/dev/null)"')

clean:
	rm -rf $(B)

# the reference: cursed-sqlite-turbo's own bench/corpus/corpus.c (TURBO_BENCH: a checkout that has it), built over the
# same two amalgamations with the same options, as exe arms for tools/identity.sh
TURBO_BENCH ?= $(TURBO)/.claude/worktrees/bench-110a4d
ref: $(B)/ref/corpus-stock $(B)/ref/corpus-turbo
.PHONY: ref
$(B)/ref/corpus-stock: $(TURBO_BENCH)/bench/corpus/corpus.c third_party/sqlite/sqlite3.c
	@mkdir -p $(B)/ref; $(CC) $(CFLAGS) $(SQLOPTS) -w -Ithird_party/sqlite $< third_party/sqlite/sqlite3.c -o $@ -lm
$(B)/ref/corpus-turbo: $(TURBO_BENCH)/bench/corpus/corpus.c $(B)/turbo/sqlite3.c
	@mkdir -p $(B)/ref; $(CC) $(CFLAGS) $(SQLOPTS) -w -I$(B)/turbo -I$(TURBO)/qc $< $(B)/turbo/sqlite3.c -o $@ -lm

# turso: adapters/turso.c over Turso's C ABI (scripts/fetch-turso.sh build: Rust, a pinned tag)
TURSO_DIR := third_party/turso
$(B)/backends/turso.so: adapters/turso.c include/sqlgauntlet.h $(TURSO_DIR)/target/release/libturso_sdk_kit.a
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -Iinclude -I$(TURSO_DIR)/sdk-kit adapters/turso.c $(TURSO_DIR)/target/release/libturso_sdk_kit.a -o $@ \
	  $(if $(filter Darwin,$(UNAME)),-framework CoreFoundation -framework Security -liconv,-lpthread -ldl -lm)
	echo "$(CC) $(CFLAGS); turso $$(git -C $(TURSO_DIR) rev-parse HEAD) cargo --release" > $@.cflags

# make test: tests/smoke.py over a few vendored apps (TEST_APPS) with stock and libSQL; make lint: what CI's lint job runs
test: all $(B)/backends/libsql.so
	python3 tests/smoke.py
lint:
	uvx pre-commit run --all-files
.PHONY: test lint
