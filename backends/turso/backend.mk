# backends/turso: adapter.c over Turso's C ABI (backends/turso/fetch.sh build: Rust, a pinned tag)
TURSO_DIR := third_party/turso
turso: $(B)/backends/turso.so
.PHONY: turso
$(B)/backends/turso.so: backends/turso/adapter.c include/sqlgauntlet.h $(TURSO_DIR)/target/release/libturso_sdk_kit.a
	@mkdir -p $(B)/backends
	$(CC) $(CFLAGS) $(SO) -Iinclude -I$(TURSO_DIR)/sdk-kit $< $(TURSO_DIR)/target/release/libturso_sdk_kit.a -o $@ \
	  $(if $(filter Darwin,$(UNAME)),-framework CoreFoundation -framework Security -liconv,-lpthread -ldl -lm)
	echo "$(CC) $(CFLAGS); turso $$(git -C $(TURSO_DIR) rev-parse HEAD) cargo --release" > $@.cflags
	$(call backend_json,turso,sqlite,)
