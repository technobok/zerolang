#!/usr/bin/env python3
"""mathtables.py -- math arc phase 3: the BigInt-level restatement of Go's
arith/nat tables. Each row names two operands by (words, pattern, sign), coded as one
number each (words*100 + pattern*10 + sign) -- the test program builds them
from the same patterns -- and carries the FNV-1a-64 digest of its results'
decimal texts joined by spaces, so a row stays one line however long the
operands.

usage: mathtables.py ops > rows.z   (ops: a comma list of add,sub,mul,div,mod)
       mathtables.py text > rows.z  (a.text base: b over sample bases)
Patterns: 0 every word MAX, 1 MAX and 0 alternating, 2 every word 2^63,
3 SplitMix64 words seeded by the length, 4 one word 1 then zeros (a power of
2^64).
"""
import sys

M64 = (1 << 64) - 1


def splitmix(seed):
    s = seed & M64
    while True:
        s = (s + 0x9E3779B97F4A7C15) & M64
        z = s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
        yield z ^ (z >> 31)


def words(n, pat):
    if pat == 0:
        return [M64] * n
    if pat == 1:
        return [M64 if i % 2 == 0 else 0 for i in range(n)]
    if pat == 2:
        return [1 << 63] * n
    if pat == 3:
        g = splitmix(n)
        return [next(g) for _ in range(n)]
    if pat == 4:
        return ([0] * (n - 1) + [1]) if n > 0 else []
    raise ValueError(pat)


def value(n, pat, neg):
    v = 0
    for w in reversed(words(n, pat)):
        v = (v << 64) | w
    return -v if neg else v


def fnv(text):
    h = 0xCBF29CE484222325
    for b in text.encode():
        h ^= b
        h = (h * 0x100000001B3) & M64
    return h


def go_divmod(a, b):
    # truncated division, Go's Quo/Rem: the remainder takes the dividend's sign
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q, a - q * b


LENGTHS = [0, 1, 2, 3, 7, 8, 9, 16, 17, 33, 40, 41, 64, 81, 100]


def rows(ops):
    out = []
    for n in LENGTHS:
        for m in sorted({0, 1, n // 2, n, n + 1}):
            for pa, pb in ((0, 0), (3, 1), (4, 3)):
                signs = ((False, False), (True, False), (False, True), (True, True))
                if n > 3:
                    signs = ((False, False), (True, False))
                for na, nb in signs:
                    a = value(n, pa, na)
                    b = value(m, pb, nb)
                    texts = []
                    for op in ops:
                        if op == "add":
                            r = a + b
                        elif op == "sub":
                            r = a - b
                        elif op == "mul":
                            r = a * b
                        elif op in ("div", "mod"):
                            if b == 0:
                                break
                            q, rm = go_divmod(a, b)
                            r = q if op == "div" else rm
                        texts.append(str(r))
                    else:
                        out.append((n, pa, na, m, pb, nb, fnv(" ".join(texts))))
    return out


DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def text(v, base):
    """v in base 2..62, Go's digits: 0-9, a-z, then A-Z above 36."""
    if v == 0:
        return "0"
    neg = v < 0
    v = abs(v)
    out = []
    while v:
        v, d = divmod(v, base)
        out.append(DIGITS[d])
    return ("-" if neg else "") + "".join(reversed(out))


TEXT_BASES = [2, 3, 7, 8, 10, 16, 32, 36, 37, 62]


def text_rows():
    out = []
    for n in LENGTHS:
        for pat in (0, 3, 4):
            for neg in (False, True):
                if n == 0 and (pat, neg) != (0, False):
                    continue
                v = value(n, pat, neg)
                for b in TEXT_BASES:
                    out.append((n * 100 + pat * 10 + int(neg), b, fnv(text(v, b))))
    return out


if __name__ == "__main__":
    if sys.argv[1] == "text":
        seen = set()
        for code, b, h in text_rows():
            if (code, b) in seen:
                continue
            seen.add((code, b))
            print(f"    row :t a: {code} base: {b} want: {h}")
        sys.exit(0)
    ops = sys.argv[1].split(",")
    seen = set()
    for n, pa, na, m, pb, nb, res in rows(ops):
        key = (n, pa, na, m, pb, nb)
        if key in seen:
            continue
        seen.add(key)
        # an operand is one number: words*100 + pattern*10 + sign
        ca = n * 100 + pa * 10 + int(na)
        cb = m * 100 + pb * 10 + int(nb)
        print(f"    row :t a: {ca} b: {cb} want: {res}")
