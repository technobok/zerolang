/* textwriter.create -- wrap a bufwriter. The sink is held by
   borrow (path-scoped lock on the bufwriter keeps it stable
   for the wrapper's lifetime). No backing buffer of our own --
   we delegate to the bufwriter's buffer. */
static z_@TextWriter@_t z_@TextWriter@_create(z_@BufWriter@_t* sink);
static z_@TextWriter@_t z_@TextWriter@_create(z_@BufWriter@_t* sink) {
    z_@TextWriter@_t self = {0};
    self.sink = sink;
    return self;
}

