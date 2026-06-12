/* bufreader.read — drain the internal buffer into `into`; when
   empty, refill from the source with a single `cap`-sized read
   (or forward straight to the source when the caller wants more
   than `cap` bytes). Returns what it has in one call; callers
   that need exactly N bytes loop externally, matching the
   reader-protocol contract ('short reads are fine'). */
static z_Result_u64_IoError_t z_BufReader_read(
    z_BufReader_t* self, z_List_u8_t* into, uint64_t max
);
static z_Result_u64_IoError_t z_BufReader_read(
    z_BufReader_t* self, z_List_u8_t* into, uint64_t max
) {
    uint64_t available = self->buf.length - self->head;
    if (available == 0) {
        if (max >= self->cap) {
            /* bypass: caller wants more than our buffer holds --
               forward directly and skip the intermediate copy. */
            return self->source.vtable->read(
                self->source.data, into, max
            );
        }
        /* refill: pull up to cap bytes into our buffer in one
           source read, then fall through to the drain path. */
        self->buf.length = 0;
        self->head = 0;
        z_Result_u64_IoError_t rr = self->source.vtable->read(
            self->source.data, &self->buf, self->cap
        );
        if (rr.tag != Z_RESULT_U64_IOERROR_TAG_OK) return rr;
        free(rr.data);
        available = self->buf.length;
        if (available == 0) return z_io_u64_ok(0);
    }
    uint64_t take = max < available ? max : available;
    if (into->capacity < into->length + take) {
        uint64_t newcap = into->length + take;
        into->data = (uint8_t*)z_xrealloc(into->data, newcap);
        into->capacity = newcap;
    }
    memcpy(
        into->data + into->length,
        self->buf.data + self->head,
        take
    );
    into->length += take;
    self->head += take;
    return z_io_u64_ok(take);
}

