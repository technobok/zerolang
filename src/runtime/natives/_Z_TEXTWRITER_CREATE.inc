/* textwriter.create -- wrap a bufwriter. The sink is held by
   borrow (path-scoped lock on the bufwriter keeps it stable
   for the wrapper's lifetime). No backing buffer of our own --
   we delegate to the bufwriter's buffer. */
static z_TextWriter_t z_TextWriter_create(z_BufWriter_t* sink);
static z_TextWriter_t z_TextWriter_create(z_BufWriter_t* sink) {
    z_TextWriter_t self = {0};
    self.sink = sink;
    return self;
}

