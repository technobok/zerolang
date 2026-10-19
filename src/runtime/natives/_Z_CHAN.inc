/* the block a channel's two ends share (sync.z Receiver / Sender): its values,
   each `elemSize` bytes, in a ring under one lock, with how many Senders live
   and whether the Receiver does. The typed ends are thin wrappers that pass
   a value by address. `cap` 0 is no limit: the ring grows when full. Of the
   Receiver and the last Sender, whichever goes second frees the block. */
typedef struct z_chan {
    z_mutex_t m;
    z_cond_t notEmpty;
    z_cond_t notFull;
    unsigned char* ring;
    size_t elemSize;
    void (*destroyElem)(void*);
    uint64_t cap;
    uint64_t size;
    uint64_t head;
    uint64_t len;
    uint64_t senders;
    bool receiverAlive;
} z_chan;

static z_chan* z_chan_new(size_t elemSize, void (*destroyElem)(void*), uint64_t capacity) {
    z_chan* b = (z_chan*)z_xmalloc(sizeof(z_chan));
    if (z_mutex_init(&b->m) != 0 || z_cond_init(&b->notEmpty) != 0 || z_cond_init(&b->notFull) != 0) {
        z_panic("sync.Receiver: the operating system could not make a lock");
    }
    b->elemSize = elemSize;
    b->destroyElem = destroyElem;
    b->cap = capacity;
    b->size = capacity != 0 ? capacity : 16;
    b->ring = (unsigned char*)z_xmalloc(elemSize * b->size);
    b->head = 0;
    b->len = 0;
    b->senders = 0;
    b->receiverAlive = true;
    return b;
}

/* the block, once neither end holds it; the lock is not held */
static void z_chan_free(z_chan* b) {
    free(b->ring);
    z_cond_destroy(&b->notFull);
    z_cond_destroy(&b->notEmpty);
    z_mutex_destroy(&b->m);
    free(b);
}

/* the ring, twice as large, its values in order from the start; the lock is
   held */
static void z_chan_grow(z_chan* b) {
    uint64_t n = b->size * 2;
    unsigned char* r = (unsigned char*)z_xmalloc(b->elemSize * n);
    for (uint64_t i = 0; i < b->len; i++) {
        memcpy(r + i * b->elemSize, b->ring + ((b->head + i) % b->size) * b->elemSize, b->elemSize);
    }
    free(b->ring);
    b->ring = r;
    b->size = n;
    b->head = 0;
}

static void z_chan_add_sender(z_chan* b) {
    z_mutex_lock(&b->m);
    b->senders++;
    z_mutex_unlock(&b->m);
}

/* a Sender ends: the last one wakes a receiver waiting for a value that can
   no longer come */
static void z_chan_drop_sender(z_chan* b) {
    z_mutex_lock(&b->m);
    b->senders--;
    bool last = b->senders == 0;
    bool gone = last && !b->receiverAlive;
    if (last) z_cond_broadcast(&b->notEmpty);
    z_mutex_unlock(&b->m);
    if (gone) z_chan_free(b);
}

/* the Receiver ends: what it never took is destroyed, and a sender waiting for
   room is woken to learn nothing will read it */
static void z_chan_drop_receiver(z_chan* b) {
    z_mutex_lock(&b->m);
    for (uint64_t i = 0; i < b->len; i++) b->destroyElem(b->ring + ((b->head + i) % b->size) * b->elemSize);
    b->len = 0;
    b->receiverAlive = false;
    bool gone = b->senders == 0;
    z_cond_broadcast(&b->notFull);
    z_mutex_unlock(&b->m);
    if (gone) z_chan_free(b);
}

/* move the value at `value` in, waiting while the ring is full; false, the
   value destroyed here, once the Receiver is gone */
static bool z_chan_send(z_chan* b, void* value) {
    z_mutex_lock(&b->m);
    while (b->receiverAlive && b->cap != 0 && b->len == b->cap) z_cond_wait(&b->notFull, &b->m);
    if (!b->receiverAlive) {
        z_mutex_unlock(&b->m);
        b->destroyElem(value);
        return false;
    }
    if (b->len == b->size) z_chan_grow(b);
    memcpy(b->ring + ((b->head + b->len) % b->size) * b->elemSize, value, b->elemSize);
    b->len++;
    z_cond_signal(&b->notEmpty);
    z_mutex_unlock(&b->m);
    return true;
}

/* the oldest value, moved to `out`: true when there was one. `wait` waits for
   one while a Sender lives; without it an empty ring answers false at once */
static bool z_chan_receive(z_chan* b, void* out, bool wait) {
    z_mutex_lock(&b->m);
    while (wait && b->len == 0 && b->senders > 0) z_cond_wait(&b->notEmpty, &b->m);
    if (b->len == 0) {
        z_mutex_unlock(&b->m);
        return false;
    }
    memcpy(out, b->ring + b->head * b->elemSize, b->elemSize);
    b->head = (b->head + 1) % b->size;
    b->len--;
    z_cond_signal(&b->notFull);
    z_mutex_unlock(&b->m);
    return true;
}
