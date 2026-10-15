/* a Row's destructor: a row owns nothing -- it reads its statement's current
   row, which the statement keeps. */
static void z_@Row@_destroy(z_@Row@_t* p) {
    (void)p;
}

