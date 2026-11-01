# backends/postgres: adapter.c over libpq (pg_config finds it), a client of a PostgreSQL server at PG_URL (the arm's
# SG_PG_URL; default a local server on 5432; backends/postgres/server.sh starts a throwaway one in Docker)
PG_URL ?= postgresql://postgres@127.0.0.1:5432
postgres: $(B)/backends/postgres.so
.PHONY: postgres
$(B)/backends/postgres.so: backends/postgres/adapter.c include/sqlgauntlet.h
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -Iinclude -I$$(pg_config --includedir) $< -o $@ -L$$(pg_config --libdir) -lpq
	echo "$(CC) $(CFLAGS); libpq $$(pg_config --version)" > $@.cflags
	$(call backend_json,postgres,postgres,SG_PG_URL=$(PG_URL))
