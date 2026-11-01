/* sqlite3ext.h: for the CLI extensions compiled into adapter.c (regexp.c, series.c, base64.c): statically linked, with
** SQLITE_EXTENSION_INIT1/2 defined empty, they need only the library's own API, not the loadable-extension table. */
#include "sqlite3.h"
