# backends/js: SQLite through JavaScript bindings, as exe arms (backends/js/driver.mjs; runtimes and packages from
# backends/js/fetch.sh): build/backends/js-<binding> for node (node:sqlite), bun (bun:sqlite), sqljs, wasm.
#   make js        fetch what's missing and write the four arms' launchers
JS_BINDINGS := node bun sqljs wasm
js:
	sh backends/js/fetch.sh
	@mkdir -p $(B)/backends
	for b in $(JS_BINDINGS); do \
	  if [ $$b = bun ]; then rt='$(CURDIR)/third_party/js/bun/bun'; else rt='$(CURDIR)/third_party/js/node/bin/node'; fi; \
	  printf '#!/bin/sh\nexec %s %s %s "$$@"\n' "$$rt" '$(CURDIR)/backends/js/driver.mjs' $$b > $(B)/backends/js-$$b; \
	  chmod +x $(B)/backends/js-$$b; done
.PHONY: js
