/* main.c: the sqlbench CLI. sqlbench --backend <backend.so> <mode> ...: loads the backend (RTLD_LOCAL, so each
** backend's engine stays private to it) and hands the rest of argv to sb_main (src/driver.c). */
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include "sqlbench.h"

int main(int argc, char **argv) {
  if (argc < 3 || strcmp(argv[1], "--backend")) { fprintf(stderr, "usage: sqlbench --backend <backend.so> build|check|hot|prep|rows|info ...\n"); return 2; }
  void *h = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
  if (!h) { fprintf(stderr, "sqlbench: %s\n", dlerror()); return 2; }
  sb_backend_fn f = (sb_backend_fn)dlsym(h, SQLBENCH_BACKEND_SYMBOL);
  if (!f) { fprintf(stderr, "sqlbench: %s has no %s\n", argv[2], SQLBENCH_BACKEND_SYMBOL); return 2; }
  argv[2] = argv[0];
  return sb_main(f(), argc - 2, argv + 2);
}
