/* bufreader.read — drain the internal buffer into `into`; when
   empty, refill from the source with a single `cap`-sized read
   (or forward straight to the source when the caller wants more
   than `cap` bytes). Returns what it has in one call; callers
   that need exactly N bytes loop externally, matching the
   reader-protocol contract ('short reads are fine'). */
static z_@ResultVR_u64_IoError@_t z_@BufReader@_read(
    z_@BufReader@_t* self, z_@ListVal_u8@_t* into, uint64_t max
);
static z_@ResultVR_u64_IoError@_t z_@BufReader@_read(
    z_@BufReader@_t* self, z_@ListVal_u8@_t* into, uint64_t max
) {
    uint64_t available = self->@MEMBER_BufReader.buf@.length - self->@MEMBER_BufReader.head@;
    if (available == 0) {
        if (max >= self->@MEMBER_BufReader.cap@) {
            /* bypass: caller wants more than our buffer holds --
               forward directly and skip the intermediate copy. */
            return self->@MEMBER_BufReader.source@.vtable->@MEMBER_Reader.read@(
                self->@MEMBER_BufReader.source@.data, into, max
            );
        }
        /* refill: pull up to cap bytes into our buffer in one
           source read, then fall through to the drain path. */
        self->@MEMBER_BufReader.buf@.length = 0;
        self->@MEMBER_BufReader.head@ = 0;
        z_@ResultVR_u64_IoError@_t rr = self->@MEMBER_BufReader.source@.vtable->@MEMBER_Reader.read@(
            self->@MEMBER_BufReader.source@.data, &self->@MEMBER_BufReader.buf@, self->@MEMBER_BufReader.cap@
        );
        if (rr.tag != @ARM_ResultVR_u64_IoError.ok@) return rr;
        available = self->@MEMBER_BufReader.buf@.length;
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
        self->@MEMBER_BufReader.buf@.data + self->@MEMBER_BufReader.head@,
        take
    );
    into->length += take;
    self->@MEMBER_BufReader.head@ += take;
    return z_io_u64_ok(take);
}


