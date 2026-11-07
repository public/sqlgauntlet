# backends/sqlite-versions: SQLite at other versions, and SQLite 3.54 with notable compile options, as SQLite-API backends
# with the same options as `sqlite` (backends/sqlite/backend.mk), so that between two arms only one thing differs.
#   make sqlite-version V=3.45.1        backend sqlite-3.45.1 (fetch.sh fetches and checks the amalgamation)
#   make sqlite-versions                every version in SQLITE_VERSIONS, one build at a time
#   make sqlite-variants                the reference's source with each option set in SQLITE_VARIANTS, as sqlite-<variant>
# Versions: what the platforms apps run on ship (Ubuntu 22.04 3.37.2, Debian 12 3.40.1, Ubuntu 24.04 3.45.1, Debian 13
# 3.46.1, libSQL's base 3.47) and the last release of each line since.
SQLITE_VERSIONS ?= 3.35.5 3.37.2 3.40.1 3.45.1 3.46.1 3.47.2 3.49.2 3.50.4 3.51.3 3.52.0 3.53.4
# Variants: options that must not change any result (a planner, storage or compiler option: a difference is a bug, or a
# tie the statement leaves open), and two that change results on purpose (dqs0: which statements rely on double-quoted
# string literals; fkeys: which writes rely on foreign keys being off, SQLite's default).
SQLITE_VARIANTS ?= stat4 sorter-refs no-autoindex null-trim O0 O3 dqs0 fkeys
VARIANT_stat4 := -DSQLITE_ENABLE_STAT4
VARIANT_sorter-refs := -DSQLITE_ENABLE_SORTER_REFERENCES
VARIANT_no-autoindex := -DSQLITE_OMIT_AUTOMATIC_INDEX
VARIANT_null-trim := -DSQLITE_ENABLE_NULL_TRIM
VARIANT_O0 := -O0
VARIANT_O3 := -O3
VARIANT_dqs0 := -USQLITE_DQS -DSQLITE_DQS=0
VARIANT_fkeys := -DSQLITE_DEFAULT_FOREIGN_KEYS=1
.PHONY: sqlite-version sqlite-versions sqlite-variant sqlite-variants

sqlite-version: $(SQLITE_API_DEPS)
	@[ -n "$(V)" ] || { echo "make sqlite-version V=X.Y.Z" >&2; exit 2; }
	sh backends/sqlite-versions/fetch.sh $(V)
	$(call sqlite_api_backend,sqlite-$(V),third_party/sqlite-$(V),,)

sqlite-versions:
	for v in $(SQLITE_VERSIONS); do [ -f $(B)/backends/sqlite-$$v.so ] || $(MAKE) -s sqlite-version V=$$v || exit 1; done

sqlite-variant: $(SQLITE_API_DEPS) third_party/sqlite/sqlite3.c
	@[ -n "$(VARIANT_$(VARIANT))" ] || { echo "make sqlite-variant VARIANT=<one of: $(SQLITE_VARIANTS)>" >&2; exit 2; }
	$(call sqlite_api_backend,sqlite-$(VARIANT),third_party/sqlite,$(VARIANT_$(VARIANT)),)

sqlite-variants:
	for v in $(SQLITE_VARIANTS); do [ -f $(B)/backends/sqlite-$$v.so ] || $(MAKE) -s sqlite-variant VARIANT=$$v || exit 1; done

# make sqlite-system: the platform's own libsqlite3 (e.g. Ubuntu 24.04's 3.45.1, with Debian's compile options), linked
# rather than compiled, as backend sqlite-system: what an app using the distribution's SQLite gets. The headers are the
# same version's amalgamation (fetch.sh); its .so.cflags records the library's PRAGMA compile_options.
SYSTEM_SQLITE ?= libsqlite3.so.0
SYSTEM_SQLITE_VERSION = $(shell python3 -c 'import sqlite3; print(sqlite3.sqlite_version)')
sqlite-system: $(SQLITE_API_DEPS)
	sh backends/sqlite-versions/fetch.sh $(SYSTEM_SQLITE_VERSION)
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -w -Iinclude -Ibackends/sqlite -Ithird_party/sqlite-$(SYSTEM_SQLITE_VERSION) -DSG_NAME='"sqlite-system"' \
	  backends/sqlite/adapter.c -l:$(SYSTEM_SQLITE) -o $(B)/backends/sqlite-system.so -lm
	python3 -c 'import sqlite3; c = sqlite3.connect(":memory:"); print("system $(SYSTEM_SQLITE) " + sqlite3.sqlite_version + ": " + \
	  " ".join(r[0] for r in c.execute("PRAGMA compile_options")))' > $(B)/backends/sqlite-system.so.cflags
	$(call backend_json,sqlite-system,sqlite,)
.PHONY: sqlite-system
