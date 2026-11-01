/* main.c: the sqlgauntlet CLI. sqlgauntlet --backend <backend.so> <mode> ...: loads the backend (RTLD_LOCAL, so each
** backend's engine stays private to it) and hands the rest of argv to sg_main (src/driver.c). */
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
#include "sqlgauntlet.h"

int main(int argc, char **argv) {
  if (argc < 3 || strcmp(argv[1], "--backend")) {
    fprintf(stderr, "usage: sqlgauntlet --backend <backend.so> build|check|hot|prep|rows|info ...\n");
    return 2;
  }
  void *h = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
  if (!h) {
    fprintf(stderr, "sqlgauntlet: %s\n", dlerror());
    return 2;
  }
  sg_backend_fn f = (sg_backend_fn)dlsym(h, SQLGAUNTLET_BACKEND_SYMBOL);
  if (!f) {
    fprintf(stderr, "sqlgauntlet: %s has no %s\n", argv[2], SQLGAUNTLET_BACKEND_SYMBOL);
    return 2;
  }
  argv[2] = argv[0];
  return sg_main(f(), argc - 2, argv + 2);
}
