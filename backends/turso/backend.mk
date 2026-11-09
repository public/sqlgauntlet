# backends/turso: adapter.c over Turso's C ABI (backends/turso/fetch.sh build: Rust, a pinned tag)
#   make turso                     the default tag (backends/turso/tags.tsv) as backend turso
#   make turso-tag TAG=<tag>       another tag from tags.tsv as backend turso-<tag> (fetch.sh build <tag> first)
TURSO_DIR := third_party/turso
TURSO_LIBS = $(if $(filter Darwin,$(UNAME)),-framework CoreFoundation -framework Security -liconv,-lpthread -ldl -lm)
# turso_backend <name> <dir>
define turso_backend
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -Iinclude -I$(2)/sdk-kit backends/turso/adapter.c $(2)/target/release/libturso_sdk_kit.a -o $(B)/backends/$(1).so $(TURSO_LIBS)
	echo "$(CC) $(CFLAGS); turso $$(git -C $(2) rev-parse HEAD) cargo --release" > $(B)/backends/$(1).so.cflags
	$(call backend_json,$(1),sqlite,)
endef
turso: $(B)/backends/turso.so
.PHONY: turso turso-tag
$(B)/backends/turso.so: backends/turso/adapter.c include/sqlgauntlet.h $(TURSO_DIR)/target/release/libturso_sdk_kit.a
	$(call turso_backend,turso,$(TURSO_DIR))
turso-tag: backends/turso/adapter.c include/sqlgauntlet.h
	@[ -n "$(TAG)" ] && [ -f third_party/turso-$(TAG)/target/release/libturso_sdk_kit.a ] || \
	  { echo "make turso-tag TAG=<tag>, after sh backends/turso/fetch.sh build <tag>" >&2; exit 2; }
	$(call turso_backend,turso-$(TAG),third_party/turso-$(TAG))
