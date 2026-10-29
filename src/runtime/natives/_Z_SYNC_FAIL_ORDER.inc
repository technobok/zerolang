/* the order a failed compare-exchange takes. It only loads, so it orders as
   its order's load half. */
static z_order_t z_sync_fail_order(z_@memoryorder@_t o);
static z_order_t z_sync_fail_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_RELEASE) return Z_RELAXED;
    if (c == Z_ACQREL) return Z_ACQUIRE;
    return c;
}
