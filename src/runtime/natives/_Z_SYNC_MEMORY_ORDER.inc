/* sync.memoryorder -- the C11 order an atomic operation takes. A load cannot
   release and a store cannot acquire: those orders are a broken program, as
   Rust treats them, so they panic rather than reach C as undefined behaviour.
   A compare-exchange that fails only loads, so it orders as its order's load
   half. */
static z_order_t z_sync_order(z_@memoryorder@_t o);
static z_order_t z_sync_order(z_@memoryorder@_t o) {
    switch (o@TAG_memoryorder@) {
        case @ARM_memoryorder.relaxed@: return Z_RELAXED;
        case @ARM_memoryorder.acquire@: return Z_ACQUIRE;
        case @ARM_memoryorder.release@: return Z_RELEASE;
        case @ARM_memoryorder.acqRel@: return Z_ACQREL;
        default: return Z_SEQCST;
    }
}

static z_order_t z_sync_load_order(z_@memoryorder@_t o);
static z_order_t z_sync_load_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_RELEASE || c == Z_ACQREL) z_panic("atomic load: a load cannot take release ordering");
    return c;
}

static z_order_t z_sync_store_order(z_@memoryorder@_t o);
static z_order_t z_sync_store_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_ACQUIRE || c == Z_ACQREL) z_panic("atomic store: a store cannot take acquire ordering");
    return c;
}

static z_order_t z_sync_fail_order(z_@memoryorder@_t o);
static z_order_t z_sync_fail_order(z_@memoryorder@_t o) {
    z_order_t c = z_sync_order(o);
    if (c == Z_RELEASE) return Z_RELAXED;
    if (c == Z_ACQREL) return Z_ACQUIRE;
    return c;
}
