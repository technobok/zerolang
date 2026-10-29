/* a store's order. A store cannot acquire: that order is a broken program,
   so it panics rather than reach C as undefined behaviour. */
static z_order_t z_sync_store_order(z_@memoryorder@_t o);
static z_order_t z_sync_store_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_ACQUIRE || c == Z_ACQREL) z_panic("atomic store: a store cannot take acquire ordering");
    return c;
}
