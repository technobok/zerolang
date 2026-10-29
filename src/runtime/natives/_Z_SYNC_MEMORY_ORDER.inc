/* sync.memoryorder -- the C11 order an atomic operation takes. */
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
