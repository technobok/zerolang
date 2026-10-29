/* a load's order. A load cannot release: that order is a broken program, so
   it panics rather than reach C as undefined behaviour. */
static z_order_t z_sync_load_order(z_@memoryorder@_t o);
static z_order_t z_sync_load_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_RELEASE || c == Z_ACQREL) z_panic("atomic load: a load cannot take release ordering");
    return c;
}
