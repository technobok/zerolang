/* SipHash-1-3 with a per-process random seed.

   Used by Map / Set bucket dispatch (variable-length keys) and the
   `String.hash` / `StringView.hash` natives. Integer hashing routes
   through `z_hash_u64` (splitmix64 finalizer) instead -- adversarial
   risk on bare integer keys is bounded by the absence of a length
   axis, and splitmix64 is materially faster on 8 bytes than SipHash.

   Seed bytes are read once at program startup via `z_siphash_init`
   (called from `main` before `z_main`). If `getrandom` and
   `/dev/urandom` both fail, the seed stays zero -- hashes are
   deterministic in that degenerate environment but the program runs
   and Map / Set behaviour is unaffected. */

static uint64_t z_sip_k0 = 0;
static uint64_t z_sip_k1 = 0;

static void z_siphash_init(void) {
    uint8_t key[16] = {0};
    ssize_t n = -1;
#ifdef SYS_getrandom
    do {
        n = (ssize_t)syscall(SYS_getrandom, key, (size_t)sizeof(key), 0);
    } while (n < 0 && errno == EINTR);
#else
    n = getrandom(key, sizeof(key), 0);
#endif
    if (n != (ssize_t)sizeof(key)) {
        size_t got = 0;
        FILE* f = fopen("/dev/urandom", "rb");
        if (f) {
            got = fread(key, 1, sizeof(key), f);
            fclose(f);
        }
        /* no entropy source: fall back to a fixed seed (hashing stays
           correct, only DoS resistance is lost) */
        if (got != sizeof(key)) memset(key, 0x5a, sizeof(key));
    }
    memcpy(&z_sip_k0, &key[0], 8);
    memcpy(&z_sip_k1, &key[8], 8);
}

#define Z_SIP_ROTL(x, b) (((x) << (b)) | ((x) >> (64 - (b))))
#define Z_SIP_ROUND(v0, v1, v2, v3) do {                       \
    v0 += v1; v1 = Z_SIP_ROTL(v1, 13); v1 ^= v0;               \
    v0 = Z_SIP_ROTL(v0, 32);                                   \
    v2 += v3; v3 = Z_SIP_ROTL(v3, 16); v3 ^= v2;               \
    v0 += v3; v3 = Z_SIP_ROTL(v3, 21); v3 ^= v0;               \
    v2 += v1; v1 = Z_SIP_ROTL(v1, 17); v1 ^= v2;               \
    v2 = Z_SIP_ROTL(v2, 32);                                   \
} while (0)

static uint64_t z_siphash_bytes(const void* in, uint64_t inlen) {
    const uint8_t* p = (const uint8_t*)in;
    uint64_t v0 = z_sip_k0 ^ 0x736f6d6570736575ULL;
    uint64_t v1 = z_sip_k1 ^ 0x646f72616e646f6dULL;
    uint64_t v2 = z_sip_k0 ^ 0x6c7967656e657261ULL;
    uint64_t v3 = z_sip_k1 ^ 0x7465646279746573ULL;
    uint64_t b = ((uint64_t)inlen) << 56;
    const uint8_t* end = p + (inlen - (inlen & 7));
    for (; p != end; p += 8) {
        uint64_t m;
        memcpy(&m, p, 8);
        v3 ^= m;
        Z_SIP_ROUND(v0, v1, v2, v3);
        v0 ^= m;
    }
    switch (inlen & 7) {
        case 7: b |= ((uint64_t)p[6]) << 48; /* fall through */
        case 6: b |= ((uint64_t)p[5]) << 40; /* fall through */
        case 5: b |= ((uint64_t)p[4]) << 32; /* fall through */
        case 4: b |= ((uint64_t)p[3]) << 24; /* fall through */
        case 3: b |= ((uint64_t)p[2]) << 16; /* fall through */
        case 2: b |= ((uint64_t)p[1]) << 8;  /* fall through */
        case 1: b |= ((uint64_t)p[0]);       /* fall through */
        case 0: break;
    }
    v3 ^= b;
    Z_SIP_ROUND(v0, v1, v2, v3);
    v0 ^= b;
    v2 ^= 0xff;
    Z_SIP_ROUND(v0, v1, v2, v3);
    Z_SIP_ROUND(v0, v1, v2, v3);
    Z_SIP_ROUND(v0, v1, v2, v3);
    return v0 ^ v1 ^ v2 ^ v3;
}

static uint64_t z_siphash_string(z_String_t s) {
    return z_siphash_bytes(s.data, s.size);
}

static uint64_t z_siphash_stringview(z_StringView_t v) {
    return z_siphash_bytes(v.data, v.length);
}

/* wyhash-style fast path, selected by `zc --fast-hash` for Map / Set
   bucket dispatch. Seeded from the same per-process entropy as
   SipHash but NOT hash-flood resistant: use only when map keys are
   trusted input. SipHash stays the default. */

static inline uint64_t z_fh_mix(uint64_t a, uint64_t b) {
    __uint128_t m = (__uint128_t)a * b;
    return (uint64_t)m ^ (uint64_t)(m >> 64);
}

static inline uint64_t z_fh_r8(const uint8_t* p) {
    uint64_t v;
    memcpy(&v, p, 8);
    return v;
}

static inline uint64_t z_fh_r4(const uint8_t* p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

static uint64_t z_fasthash_bytes(const void* in, uint64_t inlen) {
    const uint64_t c0 = 0xa0761d6478bd642fULL;
    const uint64_t c1 = 0xe7037ed1a0b428dbULL;
    const uint64_t c2 = 0x8ebc6af09c88c6e3ULL;
    const uint64_t c3 = 0x589965cc75374cc3ULL;
    const uint8_t* p = (const uint8_t*)in;
    uint64_t seed = z_sip_k0 ^ c0;
    uint64_t i = inlen;
    uint64_t a, b;
    if (i <= 16) {
        if (i >= 4) {
            a = (z_fh_r4(p) << 32) | z_fh_r4(p + ((i >> 3) << 2));
            b = (z_fh_r4(p + i - 4) << 32) | z_fh_r4(p + i - 4 - ((i >> 3) << 2));
        } else if (i > 0) {
            a = (((uint64_t)p[0]) << 16) | (((uint64_t)p[i >> 1]) << 8) | p[i - 1];
            b = 0;
        } else {
            a = 0;
            b = 0;
        }
    } else {
        if (i > 48) {
            uint64_t s1 = seed;
            uint64_t s2 = seed;
            do {
                seed = z_fh_mix(z_fh_r8(p) ^ c1, z_fh_r8(p + 8) ^ seed);
                s1 = z_fh_mix(z_fh_r8(p + 16) ^ c2, z_fh_r8(p + 24) ^ s1);
                s2 = z_fh_mix(z_fh_r8(p + 32) ^ c3, z_fh_r8(p + 40) ^ s2);
                p += 48;
                i -= 48;
            } while (i > 48);
            seed ^= s1 ^ s2;
        }
        while (i > 16) {
            seed = z_fh_mix(z_fh_r8(p) ^ c1, z_fh_r8(p + 8) ^ seed);
            p += 16;
            i -= 16;
        }
        a = z_fh_r8(p + i - 16);
        b = z_fh_r8(p + i - 8);
    }
    a ^= c1;
    b ^= seed;
    __uint128_t m = (__uint128_t)a * b;
    a = (uint64_t)m;
    b = (uint64_t)(m >> 64);
    return z_fh_mix(a ^ c0 ^ inlen, b ^ c1);
}

static uint64_t z_fasthash_string(z_String_t s) {
    return z_fasthash_bytes(s.data, s.size);
}

static uint64_t z_fasthash_stringview(z_StringView_t v) {
    return z_fasthash_bytes(v.data, v.length);
}

/* splitmix64 finalizer -- single-block mixer for 64-bit integer
   keys. Not SipHash: bare integers have no length axis to exploit
   so the security premium isn't worth ~5x the cost. Matches Rust's
   default Hasher for primitive types. */
static uint64_t z_hash_u64(uint64_t x) {
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    x *= 0x94d049bb133111ebULL;
    x ^= x >> 31;
    return x;
}
