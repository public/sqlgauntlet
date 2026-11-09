# backends/duckdb: adapter.c over DuckDB's C API, linked to its prebuilt shared library (backends/duckdb/fetch.sh), as a
# backend in DuckDB's own dialect: statements translated by sqlglot, data exported from the SQLite reference build
DUCKDB_DIR := third_party/duckdb
duckdb: $(B)/backends/duckdb.so
.PHONY: duckdb
$(DUCKDB_DIR)/duckdb.h:
	sh backends/duckdb/fetch.sh
$(B)/backends/duckdb.so: backends/duckdb/adapter.c include/sqlgauntlet.h $(DUCKDB_DIR)/duckdb.h
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -Iinclude -I$(DUCKDB_DIR) $< -o $@ -L$(CURDIR)/$(DUCKDB_DIR) -lduckdb -Wl,-rpath,$(CURDIR)/$(DUCKDB_DIR)
	echo "$(CC) $(CFLAGS); duckdb $$(cat $(DUCKDB_DIR)/VERSION)" > $@.cflags
	$(call backend_json,duckdb,duckdb,)
