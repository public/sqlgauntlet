# backends/sqlite: adapter.c over SQLite's C API, for SQLite itself and for any library that provides that API.
# One set of compile options for every SQLite-API backend, so that between two of them only the engine differs; the
# corpus needs double-quoted strings, FTS4/FTS5 and percentile() (it was recorded with the sqlite3 CLI, which has them);
# SQLDoom's renderer needs a deeper expression limit than the default 1,000 (only what is refused changes).
SQLITE_OPTS := -DSQLITE_MAX_EXPR_DEPTH=100000 -DSQLITE_THREADSAFE=0 -DSQLITE_DEFAULT_MEMSTATUS=0 -DSQLITE_DEFAULT_WAL_SYNCHRONOUS=1 -DSQLITE_DQS=3 \
  -DSQLITE_LIKE_DOESNT_MATCH_BLOBS -DSQLITE_USE_ALLOCA -DSQLITE_OMIT_LOAD_EXTENSION -DSQLITE_OMIT_DEPRECATED \
  -DSQLITE_OMIT_SHARED_CACHE -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_ENABLE_FTS4 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_PERCENTILE
SQLITE_API_DEPS := backends/sqlite/adapter.c backends/sqlite/regexp.c backends/sqlite/series.c backends/sqlite/base64.c backends/sqlite/sqlite3ext.h backends/sqlite/backend.mk include/sqlgauntlet.h

# sqlite_api_backend <name> <dir with sqlite3.c and sqlite3.h> <extra cflags> <env "K=V ...">; <name>.src records the
# directory, for the workloads that link SQLite's API themselves (speedtest1, SQLancer)
define sqlite_api_backend
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SQLITE_OPTS) $(SO) -w -Iinclude -Ibackends/sqlite -I$(2) $(3) -DSG_NAME='"$(1)"' \
	  backends/sqlite/adapter.c $(2)/sqlite3.c -o $(B)/backends/$(1).so -lm
	echo "$(CC) $(CFLAGS) $(SQLITE_OPTS) $(3)" > $(B)/backends/$(1).so.cflags
	$(call backend_json,$(1),sqlite,$(4))
	cd $(2) && pwd > $(CURDIR)/$(B)/backends/$(1).src
endef

sqlite: $(B)/backends/sqlite.so
.PHONY: sqlite sqlite-api
third_party/sqlite/sqlite3.c:
	sh backends/sqlite/fetch.sh
$(B)/backends/sqlite.so: $(SQLITE_API_DEPS) third_party/sqlite/sqlite3.c
	$(call sqlite_api_backend,sqlite,third_party/sqlite,,)

sqlite-api: $(SQLITE_API_DEPS)
	@[ -n "$(NAME)" ] && [ -f "$(SRC)/sqlite3.c" ] || { echo "make sqlite-api NAME=<name> SRC=<dir with sqlite3.c>" >&2; exit 2; }
	$(call sqlite_api_backend,$(NAME),$(SRC),$(EXTRA_CFLAGS),$(ENV))
