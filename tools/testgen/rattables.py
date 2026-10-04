#!/usr/bin/env python3
"""rattables.py OUT.z -- write the BigRat test from Go's rat tests at 577b91f341.

Tables are read out of the Go sources (git -C $GO_GIT show), not retyped:
setStringTests/setStringTests2, floatStringTests, ratCmpTests, ratBinTests,
setFrac64Tests, Issue31184, Issue45910, TestFloatPrec, and float64inputs (the
"long:" ones skipped, as Go does without -long). The float expectations come
from an independent oracle: Python's Fraction rounded to binary32 and binary64
once, nearest-even, in this file -- what strconv.ParseFloat(s, 32/64) gives.
"""
import os
import re
import subprocess
import sys
from fractions import Fraction

REV = '577b91f341'
# a git clone of the Go repository holding REV: GO_GIT names it
GOROOT = os.environ.get('GO_GIT', '')
if not GOROOT:
    sys.exit('rattables: set GO_GIT to a git clone of https://go.googlesource.com/go holding ' + REV)


def go(path):
    return subprocess.run(['git', '-C', GOROOT, 'show', f'{REV}:src/math/big/{path}'],
                          capture_output=True, text=True, check=True).stdout


def block(src, start):
    """the text of the Go composite literal `start` declares, comments removed: the
    data, past a `[]struct {...}` type when the literal has one"""
    i = src.index(start)
    j = src.index('{', i)
    if src[i:j].rstrip().endswith('struct'):
        j = src.index('}{', j) + 1
    depth = 0
    k = j
    while True:
        c = src[k]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return uncomment(src[j:k + 1])
        k += 1


def uncomment(text):
    """Go source without its line comments"""
    return '\n'.join(re.sub(r'//.*$', '', ln) for ln in text.split('\n'))


def gostr(s):
    """a Go string literal's contents (the tables use no escapes but \\)"""
    return s


def zstr(s):
    """a zerolang string literal"""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def parse_rat(s):
    """Go's SetString, as an oracle: the value, or None when it fails"""
    # the tables' inputs are all decimal/prefixed; Fraction reads the decimal
    # ones, and the few prefixed ones are listed with their expected output
    try:
        return Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None


def round_bin(x, mbits, ebits):
    """x rounded once to the binary format: (bits, exact). mbits stored bits"""
    sign = 1 if x < 0 else 0
    a = -x if x < 0 else x
    emax = (1 << (ebits - 1)) - 1
    emin = 1 - emax
    if a == 0:
        return sign << (mbits + ebits), True
    # the exponent e with 2^e <= a < 2^(e+1)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if Fraction(2) ** e > a:
        e -= 1
    if e < emin:
        e = emin
    q = a * Fraction(2) ** (mbits - e)
    m = q.numerator // q.denominator
    rem = q - m
    exact = rem == 0
    if rem > Fraction(1, 2) or (rem == Fraction(1, 2) and m % 2 == 1):
        m += 1
    if m == 1 << (mbits + 1):
        m >>= 1
        e += 1
    if e > emax:
        return (sign << (mbits + ebits)) | (((1 << ebits) - 1) << mbits), False
    if m < (1 << mbits):
        field = 0
        frac = m
    else:
        field = e + emax
        frac = m - (1 << mbits)
    return (sign << (mbits + ebits)) | (field << mbits) | frac, exact


def main():
    out = sys.argv[1]
    conv = go('ratconv_test.go')
    rat = go('rat_test.go')
    rows = []

    # SetString: invalid ones fail, valid ones read back as RatString
    for name in ('var setStringTests = ', 'var setStringTests2 = '):
        b = block(conv, name)
        for m in re.finditer(r'\{in: "([^"]*)"\}', b):
            rows.append(f'    setText :t text: {zstr(m.group(1))} ok: false want: ""')
        for m in re.finditer(r'\{"([^"]*)", "([^"]*)", true\}', b):
            rows.append(f'    setText :t text: {zstr(m.group(1))} ok: true want: {zstr(m.group(2))}')

    # FloatString; a negative precision is Go's 0
    b = block(conv, 'var floatStringTests = ')
    for m in re.finditer(r'\{"([^"]*)", (-?\d+), "([^"]*)"\}', b):
        p = max(int(m.group(2)), 0)
        rows.append(f'    decimalOf :t text: {zstr(m.group(1))} digits: {p} want: {zstr(m.group(3))}')

    # Issue31184: FloatString(3) reads back what was parsed
    i = conv.index('func TestIssue31184')
    for s in re.findall(r'"(-?[0-9.]+)"', conv[i:conv.index('func TestIssue45910')]):
        rows.append(f'    decimalOf :t text: {zstr(s)} digits: 3 want: {zstr(s)}')

    # Issue45910: exponents past what is computed are refused
    i = conv.index('func TestIssue45910')
    seg = uncomment(conv[i:conv.index('func TestFloatPrec')])
    for m in re.finditer(r'\{"([^"]*)", (true|false)\}', seg):
        rows.append(f'    parses :t text: {zstr(m.group(1))} ok: {m.group(2)}')

    # FloatPrec, for f and -f
    i = conv.index('func TestFloatPrec')
    seg = uncomment(conv[i:conv.index('func BenchmarkFloatPrecExact')])
    for m in re.finditer(r'\{"([^"]*)", (\d+), (true|false), "([^"]*)"\}', seg):
        f = m.group(1)
        if f == 'zero':
            f = '0'
        rows.append(f'    precision :t text: {zstr(f)} digits: {m.group(2)} exact: {m.group(3)} shown: {zstr(m.group(4))}')

    # Cmp
    b = block(rat, 'var ratCmpTests = ')
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)", (-?\d)\}', b):
        rows.append(f'    compares :t x: {zstr(m.group(1))} y: {zstr(m.group(2))} want: {m.group(3)}')

    # the binary operators, both ways, and back
    b = block(rat, 'var ratBinTests = ')
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)", "([^"]*)", "([^"]*)"\}', b):
        rows.append(f'    binary :t x: {zstr(m.group(1))} y: {zstr(m.group(2))} sum: {zstr(m.group(3))} prod: {zstr(m.group(4))}')

    # SetFrac64
    b = block(rat, 'var setFrac64Tests = ')
    for m in re.finditer(r'\{(-?\d+), (-?\d+), "([^"]*)"\}', b):
        a, d = int(m.group(1)), int(m.group(2))
        rows.append(f'    fraction64 :t a: {i64(a)} b: {i64(d)} want: {zstr(m.group(3))}')

    # float64inputs: both widths against the oracle, and the round trip
    i = conv.index('var float64inputs = ')
    seg = uncomment(conv[i:conv.index('\n}\n', i)])
    floats = 0
    for ln in seg.split('\n'):
        # a concatenated input is a "long:" one, skipped as Go skips them
        m = re.fullmatch(r'\s*"([^"]*)",\s*', ln)
        if m is None:
            continue
        s = m.group(1)
        if s.startswith('long:'):
            continue
        x = parse_rat(s)
        if x is None:
            raise SystemExit(f'oracle cannot read {s!r}')
        b64, e64 = round_bin(x, 52, 11)
        b32, e32 = round_bin(x, 23, 8)
        rows.append(f'    floats :t text: {zstr(s)} bits64: {b64} exact64: {str(e64).lower()} bits32: {b32} exact32: {str(e32).lower()}')
        floats += 1

    with open(out, 'w') as f:
        f.write(HEADER)
        f.write('\n'.join(rows))
        f.write('\n')
        f.write(FOOTER)
    print(f'{len(rows)} rows, {floats} float inputs')


def i64(v):
    """an i64 the zerolang source can spell: its minimum is a difference"""
    if v == -(1 << 63):
        return '(0 - 9223372036854775807 - 1)'
    return str(v)


HEADER = '''# math_bigrat.z -- Go's rat_test.go and ratconv_test.go for BigRat, generated
# by work/zerolang/tools/rattables.py from the Go sources: parse and text
# (setStringTests, floatStringTests, Issue31184, Issue45910), decimalPrecision
# (TestFloatPrec, f and -f), compare, the four operators both ways (ratBinTests),
# construction from two i64s (setFrac64Tests), and float64inputs at both
# widths against an exact rounding oracle, with the f64 round trip. Then Go's
# Hilbert test: the Hilbert matrix of order 10 times its inverse is the unit.

# the failures, and the checks run
Tally: class {
    checks: u64
    bad: u64
}

# same -- one check: the two texts agree
same: function {t: Tally.borrow what: StringView got: String.view want: StringView} is {
    t.checks = t.checks + 1
    if got != want then {
        t.bad = t.bad + 1
        print "FAIL \\{what}: \\{got}, want \\{want}"
    }
}

# truth -- one check of a fact
truth: function {t: Tally.borrow what: StringView ok: bool} is {
    t.checks = t.checks + 1
    if ok.not then {
        t.bad = t.bad + 1
        print "FAIL \\{what}"
    }
}

# rat -- a BigRat from text the test spells correctly
rat: function {text: StringView} out math.BigRat is {
    r: math.BigRat.parse :text
    match r case ok then return r.take case err then panic msg: "bad test text"
}

# setText -- parse: refused, or read back as `want`
setText: function {t: Tally.borrow text: StringView ok: bool want: StringView} is {
    r: math.BigRat.parse :text
    match r case ok then {
        truth :t what: text ok: ok
        if ok then same :t what: text got: r.string :want
        # isInt agrees with the denominator
        truth :t what: text ok: r.isInt == (r.den == (math.BigInt from: 1))
    } case err then {
        truth :t what: text ok: ok.not
    }
}

# parses -- whether parse accepts the text
parses: function {t: Tally.borrow text: StringView ok: bool} is {
    r: math.BigRat.parse :text
    got: false
    match r case ok then got = true case err then null
    truth :t what: text ok: got == ok
}

# decimalOf -- the value with `digits` digits after the point
decimalOf: function {t: Tally.borrow text: StringView digits: u64 want: StringView} is {
    same :t what: text got: ((rat :text).decimal :digits) :want
}

# precision -- decimalPrecision of the value and of its negation, and the
# decimal text at that many digits
precision: function {t: Tally.borrow text: StringView digits: u64 exact: bool shown: StringView} is {
    x: rat :text
    for each k: 2.u64.times loop {
        p: x.decimalPrecision
        truth :t what: text ok: (p.digits == digits) and (p.exact == exact)
        want: shown.string
        if x.sign < 0 then {
            w: "-".string
            w.append shown
            want = w.take
        }
        same :t what: text got: (x.decimal :digits) want: want.stringView
        if k == 0 when x.sign > 0 then x = x.neg
    }
}

# compares -- x.compare y
compares: function {t: Tally.borrow x: StringView y: StringView want: i32} is {
    truth :t what: x ok: ((rat text: x).compare rhs: (rat text: y)) == want
}

# binary -- x + y == sum both ways, sum - x == y and sum - y == x, x * y == prod
# both ways, and prod / x == y and prod / y == x where they divide
binary: function {t: Tally.borrow x: StringView y: StringView sum: StringView prod: StringView} is {
    a: rat text: x
    b: rat text: y
    s: rat text: sum
    p: rat text: prod
    same :t what: "x+y" got: (a + b).string want: s.string.stringView
    same :t what: "y+x" got: (b + a).string want: s.string.stringView
    same :t what: "s-x" got: (s - a).string want: b.string.stringView
    same :t what: "s-y" got: (s - b).string want: a.string.stringView
    same :t what: "x*y" got: (a * b).string want: p.string.stringView
    same :t what: "y*x" got: (b * a).string want: p.string.stringView
    if a.sign != 0 then same :t what: "p/x" got: (p / a).string want: b.string.stringView
    if b.sign != 0 then same :t what: "p/y" got: (p / b).string want: a.string.stringView
}

# fraction64 -- fromFraction over two i64s
fraction64: function {t: Tally.borrow a: i64 b: i64 want: StringView} is {
    r: math.BigRat.fromFraction num: (math.BigInt from: a) den: (math.BigInt from: b)
    same :t what: "fromFraction" got: r.string :want
}

# floats -- the value at both widths, against the oracle's bits and
# exactness, and an f64 read back as itself
floats: function {
    t: Tally.borrow
    text: StringView
    bits64: u64
    exact64: bool
    bits32: u32
    exact32: bool
} is {
    x: rat :text
    d: x.roundF64
    truth :t what: text ok: d.bits == bits64
    e64: false
    match x.f64 case ok then e64 = true case err then null
    truth :t what: text ok: e64 == exact64
    f: x.roundF32
    truth :t what: text ok: f.bits == bits32
    e32: false
    match x.f32 case ok then e32 = true case err then null
    truth :t what: text ok: e32 == exact32
    if d.isFinite then {
        back: math.BigRat.fromF64 from: d
        # compared as values, as Go compares them: a negative value too small
        # for an f64 is -0, which a rational reads back as 0
        match back case some then {
            truth :t what: text ok: back.roundF64 == d
        } case none then {
            truth :t what: text ok: false
        }
    }
}

# hilbert -- the Hilbert matrix of order n, a[i][j] = 1 / (i + j + 1)
hilbert: function {n: i64} out (List math.BigRat) is {
    a: List math.BigRat
    i: 0
    for i < n loop {
        j: 0
        for j < n loop {
            a.append (math.BigRat.fromFraction num: (math.BigInt from: 1) den: (math.BigInt from: i + j + 1))
            j = j + 1
        }
        i = i + 1
    }
    return a
}

# inverseHilbert -- its inverse, by Go's closed form of binomials
inverseHilbert: function {n: i64} out (List math.BigRat) is {
    a: List math.BigRat
    i: 0
    for i < n loop {
        j: 0
        for j < n loop {
            x: math.BigInt from: i + j + 1
            x = x * (math.BigInt.binomial n: n + i k: n - j - 1)
            x = x * (math.BigInt.binomial n: n + j k: n - i - 1)
            c: math.BigInt.binomial n: i + j k: i
            x = x * c * c
            if ((i + j) & 1) != 0 then x = x.neg
            a.append (math.BigRat.fromInt from: x)
            j = j + 1
        }
        i = i + 1
    }
    return a
}

# hilbertTest -- Go's TestHilbert: H * H^-1 is the unit matrix
hilbertTest: function {t: Tally.borrow n: i64} is {
    h: hilbert :n
    v: inverseHilbert :n
    i: 0
    for i < n loop {
        j: 0
        for j < n loop {
            x: math.BigRat from: 0
            k: 0
            for k < n loop {
                x = x + ((h.get i: (i * n + k).u64.orPanic) * (v.get i: (k * n + j).u64.orPanic))
                k = k + 1
            }
            want: math.BigRat from: 0
            if i == j then want = math.BigRat from: 1
            truth :t what: "hilbert" ok: x == want
            j = j + 1
        }
        i = i + 1
    }
}

main: function is {
    t: Tally checks: 0 bad: 0
'''

FOOTER = '''    hilbertTest :t n: 10
    if t.bad == 0 then print "ok \\{t.checks} checks" else print "\\{t.bad} of \\{t.checks} checks failed"
}
'''

if __name__ == '__main__':
    main()
