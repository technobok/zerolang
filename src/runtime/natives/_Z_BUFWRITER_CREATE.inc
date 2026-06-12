/* bufwriter.create — allocate a buffered writer with an empty
   backing buffer of the requested capacity. The sink is held by
   borrow (the source writer is locked by the typechecker's
   path-scoped lock for the wrapper's lifetime). */
static z_BufWriter_t z_BufWriter_create(z_Writer_t sink, uint64_t cap);
static z_BufWriter_t z_BufWriter_create(z_Writer_t sink, uint64_t cap) {
    z_BufWriter_t self = {0};
    self.sink = sink;
    self.buf = z_List_u8_create(cap);
    self.cap = cap;
    return self;
}

