/* bufwriter.create — allocate a buffered writer with an empty
   backing buffer of the requested capacity. The sink is held by
   borrow (the source writer is locked by the typechecker's
   path-scoped lock for the wrapper's lifetime). */
static z_@BufWriter@_t z_@BufWriter@_create(z_@Writer@_t sink, uint64_t cap);
static z_@BufWriter@_t z_@BufWriter@_create(z_@Writer@_t sink, uint64_t cap) {
    z_@BufWriter@_t self = {0};
    self.sink = sink;
    self.buf = z_@ListVal_u8@_create(cap);
    self.cap = cap;
    return self;
}

