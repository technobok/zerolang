/* bufreader.create — wrap a reader. Pre-allocates the internal
   `buf` to `cap` so the first refill never has to grow it. `head`
   starts at 0; the buffer is empty (buf.length == 0) so the first
   read triggers a refill. Source is held by borrow via the
   typechecker's path-scoped lock. */
static z_BufReader_t z_BufReader_create(z_Reader_t source, uint64_t cap);
static z_BufReader_t z_BufReader_create(z_Reader_t source, uint64_t cap) {
    z_BufReader_t self = {0};
    self.source = source;
    self.buf = z_List_u8_create(cap);
    self.head = 0;
    self.cap = cap;
    return self;
}

