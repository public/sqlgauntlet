# backends/libsql: libSQL's C amalgamation (a SQLite fork) through backends/sqlite's adapter
libsql: $(B)/backends/libsql.so
.PHONY: libsql
third_party/libsql/sqlite3.c:
	sh backends/libsql/fetch.sh
$(B)/backends/libsql.so: $(SQLITE_API_DEPS) third_party/libsql/sqlite3.c
	$(call sqlite_api_backend,libsql,third_party/libsql,,)
