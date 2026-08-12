/* bufreader.create — wrap a reader. Pre-allocates the internal
   `buf` to `cap` so the first refill never has to grow it. `head`
   starts at 0; the buffer is empty (buf.length == 0) so the first
   read triggers a refill. Source is held by borrow via the
   typechecker's path-scoped lock. */
static z_@BufReader@_t z_@BufReader@_create(z_@Reader@_t source, uint64_t cap);
static z_@BufReader@_t z_@BufReader@_create(z_@Reader@_t source, uint64_t cap) {
    z_@BufReader@_t self = {0};
    self.source = source;
    self.buf = z_@ListVal_u8@_create(cap);
    self.head = 0;
    self.cap = cap;
    return self;
}


