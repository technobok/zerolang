#!/usr/bin/env python3
"""floattables.py OUTDIR -- write the BigFloat tests from Go's float tests at 577b91f341.

Two programs: math_bigfloat.z (float_test.go, bits_test.go, sqrt_test.go) and
math_bigfloat_text.z (floatconv_test.go and decimal_test.go through the public
text method). Tables are read out of the Go sources (git -C $GO_GIT show),
not retyped. Go's float64/float32 operands become bit patterns rounded once by
the exact oracle in rattables.py; the Float32/Float64 conversion tables are
checked against that oracle, and the table's own accuracy against it here.
Format and Scan (the fmt package's verbs, widths and flags) have no
counterpart and are not ported; the decimal type's own tests are restated
through text.
"""
import os
import re
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rattables import go, block, uncomment, zstr, round_bin  # noqa: E402

MODES = {
    'ToNearestEven': 'toNearestEven', 'ToNearestAway': 'toNearestAway', 'ToZero': 'toZero',
    'AwayFromZero': 'awayFromZero', 'ToNegativeInf': 'toNegativeInf', 'ToPositiveInf': 'toPositiveInf',
}
ACCS = {'Below': 'below', 'Exact': 'exact', 'Above': 'above'}
FORMATS = {
    'e': 'exponent', 'E': 'exponentUpper', 'f': 'fixed', 'g': 'general', 'G': 'generalUpper',
    'x': 'hex', 'p': 'hexFraction', 'b': 'binary',
}
INF = float('inf')
MAXEXP = 2147483647
MINEXP = -2147483648


def mode(name):
    return f'math.roundingmode.{MODES[name]}'


def acc(name):
    return f'math.accuracy.{ACCS[name]}'


def gofloat(s):
    """the value of a Go number text as Float.Parse reads it (base 0): a Fraction,
    or +-INF"""
    t = s.replace('_', '')
    neg = t.startswith('-')
    if t[:1] in '+-':
        t = t[1:]
    if t in ('Inf', 'inf'):
        return -INF if neg else INF
    m = re.fullmatch(r'0([xXbBoO])([0-9a-fA-F]*)(?:\.([0-9a-fA-F]*))?(?:[pP]([+-]?\d+)|[eE]([+-]?\d+))?', t)
    if m and not (m.group(1) in 'bBoO' and m.group(5) is None and False):
        base = {'x': 16, 'X': 16, 'b': 2, 'B': 2, 'o': 8, 'O': 8}[m.group(1)]
        ip, fp = m.group(2) or '', m.group(3) or ''
        v = Fraction(int(ip + fp or '0', base), base ** len(fp))
        if m.group(4) is not None:
            v *= Fraction(2) ** int(m.group(4))
        if m.group(5) is not None:
            v *= Fraction(10) ** int(m.group(5))
    else:
        m = re.fullmatch(r'([0-9]*)(?:\.([0-9]*))?(?:[pP]([+-]?\d+)|[eE]([+-]?\d+))?', t)
        if m is None:
            raise SystemExit(f'cannot read Go number {s!r}')
        ip, fp = m.group(1) or '', m.group(2) or ''
        v = Fraction(int(ip + fp or '0'), 10 ** len(fp))
        if m.group(3) is not None:
            v *= Fraction(2) ** int(m.group(3))
        if m.group(4) is not None:
            v *= Fraction(10) ** int(m.group(4))
    return -v if neg else v


def f64bits(v):
    """the binary64 pattern nearest v (a Fraction, +-INF, or the string '-0')"""
    if v == '-0':
        return 1 << 63
    if v == INF:
        return 0x7ff << 52
    if v == -INF:
        return 0xfff << 52
    return round_bin(Fraction(v), 52, 11)[0]


def f32bits(v):
    if v == '-0':
        return 1 << 31
    if v == INF:
        return 0xff << 23
    if v == -INF:
        return 0x1ff << 23
    return round_bin(Fraction(v), 23, 8)[0]


def f64of(bits):
    """the value a binary64 pattern encodes, as a Fraction or +-INF or '-0'"""
    sign = bits >> 63
    e = (bits >> 52) & 0x7ff
    m = bits & ((1 << 52) - 1)
    if e == 0x7ff:
        return -INF if sign else INF
    if e == 0:
        v = Fraction(m, 1 << 1074)
    else:
        v = Fraction(m | (1 << 52)) * Fraction(2) ** (e - 1075)
    if sign:
        return '-0' if v == 0 else -v
    return v


def f32of(bits):
    sign = bits >> 31
    e = (bits >> 23) & 0xff
    m = bits & ((1 << 23) - 1)
    if e == 0xff:
        return -INF if sign else INF
    if e == 0:
        v = Fraction(m, 1 << 149)
    else:
        v = Fraction(m | (1 << 23)) * Fraction(2) ** (e - 150)
    if sign:
        return '-0' if v == 0 else -v
    return v


class GoFloat:
    """a Go float64 operand: the value rounded once to binary64"""

    def __init__(self, bits):
        self.bits = bits


def goexpr(expr):
    """the binary64 bits of a Go float64 expression from the tables: constant
    arithmetic is exact and rounds once, fdiv divides two float64s"""
    e = expr.strip()
    special = {
        '-zero_': '-0', 'math.Copysign(0, -1)': '-0',
        'math.Inf(-1)': -INF, '-math.Inf(1)': -INF, '-math.Inf(0)': -INF, '-inf': -INF,
        'math.Inf(0)': INF, 'math.Inf(+1)': INF, 'math.Inf(1)': INF, 'inf': INF,
    }
    if e in special:
        return f64bits(special[e])
    m = re.fullmatch(r'fdiv\((.*), (.*)\)', e)
    if m:
        a = float(f64of(goexpr(m.group(1))))
        b = float(f64of(goexpr(m.group(2))))
        q = a / b
        return f64bits('-0' if q == 0 and str(q).startswith('-') else Fraction(q))
    names = {
        'math.MaxFloat32': Fraction(2) ** 127 * (2 - Fraction(1, 2 ** 23)),
        'math.MaxFloat64': Fraction(2) ** 1023 * (2 - Fraction(1, 2 ** 52)),
        'math.SmallestNonzeroFloat32': Fraction(1, 2 ** 149),
        'math.SmallestNonzeroFloat64': Fraction(1, 2 ** 1074),
        'smallestNormalFloat64': gofloat('2.2250738585072014e-308'),
        'below1e23': Fraction(99999999999999974834176),
        'above1e23': Fraction(100000000000000008388608),
    }

    def lit(mm):
        return f'F({mm.group(0)!r})'
    src = e
    for k in sorted(names, key=len, reverse=True):
        src = src.replace(k, f'N[{k!r}]')
    src = re.sub(r"(?<![\w'\[])(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)", lit, src)
    def num(t):
        if t[:2] in ('0x', '0X') or t.isdigit():
            return int(t, 0) if t[:2] in ('0x', '0X') else int(t)
        return Fraction(t)
    v = eval(src, {'F': num, 'N': names})
    return f64bits(Fraction(v))


def hexu(v):
    return f'0x{v:x}'


def rows_float(ft):
    rows = []

    # TestFloatZeroValue: x op y into a zero value (precision 0) or a 64-bit z
    i = ft.index('func TestFloatZeroValue')
    seg = uncomment(ft[i:ft.index('func makeFloat')])
    ops = {'+': 0, '-': 1, '*': 2, '/': 3}
    for m in re.finditer(r"\{(\d), (\d), (\d), (-?\d), '(.)', \(\*Float\)\.\w+\}", seg):
        z, x, y, want, op = m.groups()
        rows.append(f'    zeroValue :t z: {z} x: {x} y: {y} want: {want} op: {ops[op]}')

    # TestFloatSetPrec
    i = ft.index('func TestFloatSetPrec')
    seg = uncomment(ft[i:ft.index('func TestFloatMinPrec')])
    for m in re.finditer(r'\{"([^"]*)", (\w+), "([^"]*)", (\w+)\}', seg):
        x, p, want, a = m.groups()
        p = {'MaxPrec': 4294967295, '1e6': 1000000}.get(p, p)
        rows.append(f'    setPrec :t x: {zstr(x)} prec: {p} want: {zstr(want)} acc: {acc(a)}')

    # TestFloatMinPrec
    i = ft.index('func TestFloatMinPrec')
    seg = uncomment(ft[i:ft.index('func TestFloatSign')])
    for m in re.finditer(r'\{"([^"]*)", (\w+)\}', seg):
        x, w = m.groups()
        w = 100 if w == 'max' else w
        rows.append(f'    minPrec :t x: {zstr(x)} want: {w}')

    # TestFloatSign
    i = ft.index('func TestFloatSign')
    seg = uncomment(ft[i:ft.index('func alike')])
    for m in re.finditer(r'\{"([^"]*)", ([+-]?\d)\}', seg):
        rows.append(f'    signOf :t x: {zstr(m.group(1))} want: {int(m.group(2))}')

    # TestFloatMantExp
    i = ft.index('func TestFloatMantExp(')
    seg = uncomment(ft[i:ft.index('func TestFloatMantExpAliasing')])
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)", (-?\d+)\}', seg):
        rows.append(f'    mantExp :t x: {zstr(m.group(1))} mant: {zstr(m.group(2))} exp: {m.group(3)}')
    rows.append('    mantExp :t x: "0.5p10" mant: "0.5" exp: 10')

    # TestFloatSetMantExp
    i = ft.index('func TestFloatSetMantExp')
    seg = uncomment(ft[i:ft.index('func TestFloatPredicates')])
    for m in re.finditer(r'\{"([^"]*)", ([^,]+), "([^"]*)"\}', seg):
        frac, e, z = m.groups()
        e = eval(e.replace('MinExp', str(MINEXP)).replace('MaxExp', str(MAXEXP)))
        rows.append(f'    setMantExp :t frac: {zstr(frac)} exp: {e} want: {zstr(z)}')

    # TestFloatPredicates
    i = ft.index('func TestFloatPredicates')
    seg = uncomment(ft[i:ft.index('func TestFloatIsInt')])
    for m in re.finditer(r'\{x: "([^"]*)"([^}]*)\}', seg):
        x, rest = m.groups()
        sg = re.search(r'sign: (-?\d)', rest)
        sb = 'signbit: true' in rest
        inf = 'inf: true' in rest
        rows.append(f'    predicates :t x: {zstr(x)} sign: {sg.group(1) if sg else 0} '
                    f'signbit: {str(sb).lower()} inf: {str(inf).lower()}')

    # TestFloatIsInt
    i = ft.index('func TestFloatIsInt')
    seg = uncomment(ft[i:ft.index('func fromBinary')])
    seg = seg[:seg.index('} {')]
    for s in re.findall(r'"([^"]*)"', seg):
        if s == ' int':
            continue
        x = s.removesuffix(' int')
        rows.append(f'    isInt :t x: {zstr(x)} want: {str(x != s).lower()}')

    # TestFloatRound: every row in the six modes, and the negated ones
    i = ft.index('func TestFloatRound(')
    seg = uncomment(ft[i:ft.index('func TestFloatRound24')])
    for m in re.finditer(r'\{(\d), "(\d+)", "(\d+)", "(\d+)", "(\d+)", "(\d+)"\}', seg):
        p = int(m.group(1))
        x, z, e, n, a = (int(v, 2) for v in m.groups()[1:])
        for xx, rr, md in ((x, z, 'ToZero'), (x, e, 'ToNearestEven'), (x, n, 'ToNearestAway'),
                           (x, a, 'AwayFromZero'), (x, z, 'ToNegativeInf'), (x, a, 'ToPositiveInf'),
                           (-x, -a, 'ToNegativeInf'), (-x, -z, 'ToPositiveInf')):
            rows.append(f'    roundCase :t x: {xx} r: {rr} prec: {p} mode: {mode(md)}')

    # TestFloatRound24: a float64 rounded to 24 bits is the float32 conversion
    for d in range(0x11):
        x = (1 << 26) - 0x10 + d
        rows.append(f'    round24 :t x: {x} want: {hexu(f32bits(Fraction(x)))}')

    # TestFloatSetFloat64, both signs
    i = ft.index('func TestFloatSetFloat64')
    seg = uncomment(ft[i:ft.index('// test basic rounding behavior', i)])
    body = seg[seg.index('[]float64{') + 10:seg.index('} {')]
    for item in body.split(',\n'):
        item = item.strip().rstrip(',')
        if not item:
            continue
        b = goexpr(item)
        rows.append(f'    setFloat64 :t bits: {hexu(b)}')
        rows.append(f'    setFloat64 :t bits: {hexu(b ^ (1 << 63))}')

    # TestFloatSetInt and TestFloatSetRat
    i = ft.index('func TestFloatSetInt(')
    seg = uncomment(ft[i:ft.index('func TestFloatSetRat')])
    for s in re.findall(r'"(-?\d+)"', seg):
        rows.append(f'    setInt :t text: {zstr(s)}')
    i = ft.index('func TestFloatSetRat')
    seg = uncomment(ft[i:ft.index('func TestFloatSetInf')])
    for s in re.findall(r'"(-?[\d.]+)"', seg):
        rows.append(f'    setRat :t text: {zstr(s)}')

    # TestFloatSetInf
    i = ft.index('func TestFloatSetInf')
    seg = uncomment(ft[i:ft.index('func TestFloatUint64')])
    for m in re.finditer(r'\{(true|false), (\d+), "([^"]*)"\}', seg):
        rows.append(f'    setInf :t negative: {m.group(1)} prec: {m.group(2)} want: {zstr(m.group(3))}')

    # TestFloatUint64 and TestFloatInt64: the exact conversion, the truncation,
    # and the accuracy as the clamped result compares to x
    for name, nxt, fn, lo, hi in (('func TestFloatUint64', 'func TestFloatInt64', 'toU64', 0, (1 << 64) - 1),
                                  ('func TestFloatInt64', 'func TestFloatFloat32', 'toI64', -(1 << 63), (1 << 63) - 1)):
        i = ft.index(name)
        seg = uncomment(ft[i:ft.index(nxt)])
        for m in re.finditer(r'\{"([^"]*)", ([^,]+), (\w+)\}', seg):
            x, out, a = m.groups()
            out = {'math.MaxUint64': (1 << 64) - 1, 'math.MaxInt64': (1 << 63) - 1,
                   'math.MinInt64': -(1 << 63)}.get(out.strip()) if not re.fullmatch(r'-?\d+', out.strip()) else int(out)
            v = gofloat(x)
            if v in (INF, -INF):
                kind = 'outOfRange'
            else:
                tr = int(v)  # toward zero
                if not (lo <= tr <= hi):
                    kind = 'outOfRange'
                elif v.denominator != 1:
                    kind = 'precisionLoss'
                else:
                    kind = 'ok'
            if (kind == 'ok') != (a == 'Exact'):
                raise SystemExit(f'{fn} {x}: table accuracy {a} disagrees with {kind}')
            o = str(out) if out != -(1 << 63) else '(0 - 9223372036854775807 - 1)'
            if fn == 'toU64':
                o = hexu(out)
            rows.append(f'    {fn} :t x: {zstr(x)} want: {o} acc: {acc(a)} kind: {zstr(kind)}')

    # TestFloatFloat32 and TestFloatFloat64, both signs, against the oracle
    for name, nxt, fn, rb in (('func TestFloatFloat32', 'func TestFloatFloat64', 'toF32', f32bits),
                              ('func TestFloatFloat64', 'func TestFloatInt(', 'toF64', f64bits)):
        i = ft.index(name)
        seg = uncomment(ft[i:ft.index(nxt)])
        for m in re.finditer(r'\{"([^"]*)", [^\n]*, (Below|Exact|Above)\}', seg):
            for neg in (False, True):
                x = ('-' if neg else '') + m.group(1)
                a = m.group(2)
                if neg:
                    a = {'Below': 'Above', 'Above': 'Below', 'Exact': 'Exact'}[a]
                v = gofloat(x)
                b = rb('-0' if (neg and v == 0) else v)
                back = (f32of if fn == 'toF32' else f64of)(b)
                if v in (INF, -INF):
                    oa = 'Exact'
                else:
                    bv = Fraction(0) if back == '-0' else back
                    if bv in (INF, -INF):
                        oa = 'Above' if bv == INF else 'Below'
                    else:
                        oa = 'Exact' if bv == v else ('Above' if bv > v else 'Below')
                if oa != a:
                    raise SystemExit(f'{fn} {x}: oracle accuracy {oa}, table {a}')
                rows.append(f'    {fn} :t x: {zstr(x)} bits: {hexu(b)} acc: {acc(a)}')

    # TestFloatInt
    i = ft.index('func TestFloatInt(')
    seg = uncomment(ft[i:ft.index('// check that supplied *Int is used')])
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)", (\w+)\}', seg):
        rows.append(f'    toInt :t x: {zstr(m.group(1))} want: {zstr(m.group(2))} acc: {acc(m.group(3))}')

    # TestFloatRat
    i = ft.index('func TestFloatRat')
    seg = uncomment(ft[i:ft.index('// check that supplied *Rat is used')])
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)", (\w+)\}', seg):
        rows.append(f'    toRat :t x: {zstr(m.group(1))} want: {zstr(m.group(2))} acc: {acc(m.group(3))}')

    # TestFloatAbs and TestFloatNeg
    i = ft.index('func TestFloatAbs')
    seg = uncomment(ft[i:ft.index('func TestFloatNeg')])
    seg = seg[:seg.index('} {')]
    for s in re.findall(r'"([^"-][^"]*)"', seg):
        rows.append(f'    absNeg :t x: {zstr(s)}')

    # TestFloatMul64 operands
    i = ft.index('func TestFloatMul64')
    seg = uncomment(ft[i:ft.index('func TestIssue6866')])
    for m in re.finditer(r'\{([^{},]+), ([^{},]+)\},', seg):
        rows.append(f'    mul64 :t x: {hexu(goexpr(m.group(1)))} y: {hexu(goexpr(m.group(2)))}')

    # TestFloatArithmeticOverflow
    i = ft.index('func TestFloatArithmeticOverflow')
    seg = uncomment(ft[i:ft.index('func TestFloatArithmeticRounding')])
    for m in re.finditer(r"\{(\d+), (\w+), '(.)', \"([^\"]*)\", \"([^\"]*)\", \"([^\"]*)\", (\w+)\}", seg):
        p, md, op, x, y, want, a = m.groups()
        rows.append(f'    overflow :t prec: {p} mode: {mode(md)} op: {ops[op]} x: {zstr(x)} y: {zstr(y)} '
                    f'want: {zstr(want)} acc: {acc(a)}')

    # TestFloatArithmeticRounding
    i = ft.index('func TestFloatArithmeticRounding')
    seg = uncomment(ft[i:ft.index('func TestFloatCmpSpecialValues')])
    for m in re.finditer(r"\{(\w+), (\d+), (-?0x[0-9a-f]+), (-?0x[0-9a-f]+), (-?0x[0-9a-f]+), '(.)'\}", seg):
        md, p, x, y, want, op = m.groups()
        rows.append(f'    arithRounding :t mode: {mode(md)} prec: {p} x: {int(x, 16)} y: {int(y, 16)} '
                    f'want: {int(want, 16)} op: {ops[op]}')

    # bits_test.go TestFromBits
    bt = go('bits_test.go')
    i = bt.index('func TestFromBits')
    seg = uncomment(bt[i:])
    for m in re.finditer(r'\{(nil|Bits\{[^}]*\}|append\(Bits\{([^}]*)\}, Bits\{([^}]*)\} \.\.\.\)), "([^"]*)"\}', seg):
        if m.group(1) == 'nil':
            bits = []
        elif m.group(1).startswith('append'):
            bits = [int(v) for v in (m.group(2) + ',' + m.group(3)).split(',') if v.strip()]
        else:
            bits = [int(v) for v in m.group(1)[5:-1].split(',') if v.strip()]
        rows.append(f'    fromBits :t bits: {zlist(bits)} want: {zstr(m.group(4))}')

    # ieeeBits at binary16 and binary128 against the oracle, and back through
    # fromIEEEBits: the formats the compiler's f16 and f128 constants take.
    # Go has no such test; the values are its float64inputs' edges and each
    # format's own
    edges = ['0', '-0', '1', '-1', '0.1', '-0.1', '3.14159265358979323846264338327950288419716939937510',
             '65504', '65519.99', '65520', '-65520', '6.103515625e-05', '6.0975551605224609375e-05',
             '5.9604644775390625e-08', '2.98023223876953125e-08', '2.98023223876953126e-08', '1e-08',
             '1.18973149535723176508575932662800702e4932', '1.1897314953572317650857593266280071e4932',
             '1e4933', '3.3621031431120935062626778173217526e-4932',
             '6.475175119438025110924438958227646552e-4966', '3.2375875597190125554e-4966',
             '3.2375875597190125555e-4966', '1e-5000', '1e300', '-1e-300', '0x1.fffffffffffffp1023',
             '1.0000000000000000000000000000000001', '1.00000000000000000000000000000000019', 'Inf', '-Inf',
             '1.00048828125', '1.000488281250000000000000000000001', '1.00146484375',
             '1.0000000000000000000000000000000000962964972193617926527988971292463659',
             '1.00000000000000000000000000000000009629649721936179265279889712924636592',
             '2.98023223876953125e-08', '-8.940696716308594e-08']
    for x in edges:
        v = gofloat(x)
        neg = x.startswith('-')
        b16 = ieee(v, neg, 10, 5)
        b128 = ieee(v, neg, 112, 15)
        r16 = r128 = '""'
        if v not in (INF, -INF):
            r16 = zstr(hex(ieee(v, False, 10, 5)))
            r128 = zstr(hex(ieee(v, False, 112, 15)))
        rows.append(f'    ieeeCase :t x: {zstr(x)} bits16: {zstr(hex(b16))} bits128: {zstr(hex(b128))} '
                    f'rat16: {r16} rat128: {r128}')

    # sqrt_test.go TestFloatSqrt
    st = go('sqrt_test.go')
    i = st.index('func TestFloatSqrt(')
    seg = uncomment(st[i:st.index('func TestFloatSqrtSpecial')])
    for m in re.finditer(r'\{"([^"]*)", "([^"]*)"\}', seg):
        rows.append(f'    sqrtCase :t x: {zstr(m.group(1))} want: {zstr(m.group(2))}')
    return rows


def ieee(v, neg, mbits, ebits):
    """the pattern nearest v in a binary format, a zero or an infinity keeping
    its sign"""
    sign = 1 << (mbits + ebits)
    allone = ((1 << ebits) - 1) << mbits
    if v in (INF, -INF):
        return allone | (sign if v == -INF else 0)
    if v == 0:
        return sign if neg else 0
    b, _ = round_bin(Fraction(v), mbits, ebits)
    return b


def zlist(bits):
    return '(bitsOf ' + ' '.join(['n: ' + str(len(bits))] + [f'b{k}: {b}' for k, b in enumerate(bits)]) + ')'


def rows_text(fc, dc):
    rows = []

    # TestFloatSetFloat64String
    i = fc.index('func TestFloatSetFloat64String')
    seg = uncomment(fc[i:fc.index('func fdiv')])
    for m in re.finditer(r'\{"([^"]*)", ([^}]+)\},', seg):
        s, x = m.groups()
        if x.strip() == 'nan':
            rows.append(f'    readsFloat :t text: {zstr(s)} ok: false bits: 0')
        else:
            rows.append(f'    readsFloat :t text: {zstr(s)} ok: true bits: {hexu(goexpr(x))}')

    # TestFloat64Text, each value at the precision it actually has
    i = fc.index('func TestFloat64Text')
    seg = uncomment(fc[i:fc.index('func actualPrec')])
    for m in re.finditer(r"\{([^{}\n]+), '(.)', (-?\d+), \"([^\"]*)\"\}", seg):
        x, f, p, want = m.groups()
        b = goexpr(x)
        e = (b >> 52) & 0x7ff
        mm = b & ((1 << 52) - 1)
        ap = mm.bit_length() if (e == 0 and mm != 0) else 53
        rows.append(f'    f64Text :t bits: {hexu(b)} prec: {ap} format: math.floatformat.{FORMATS[f]} '
                    f'digits: {p} want: {zstr(want)}')

    # TestRoundShortestNormal: Go checks these against strconv's shortest
    # spelling, which for these values is also Python's repr
    i = fc.index('func TestRoundShortestNormal')
    seg = uncomment(fc[i:fc.index('func TestFloatText')])
    for x in re.findall(r'\n\t\t([0-9.e+]+),', seg):
        r = repr(float(x))
        if 'e+' not in r:
            raise SystemExit(f'repr {r} is not in exponent form')
        rows.append(f'    f64Text :t bits: {hexu(f64bits(Fraction(x)))} prec: 53 format: math.floatformat.general '
                    f'digits: -1 want: {zstr(r)}')

    # TestFloatText
    i = fc.index('func TestFloatText')
    seg = uncomment(fc[i:fc.index('func TestFloatFormat')])
    for m in re.finditer(r"\{\"([^\"]*)\", (\w+), (\d+), '(.)', (-?\d+), \"([^\"]*)\"\}", seg):
        x, rnd, p, f, d, want = m.groups()
        md = 'none' if rnd == 'defaultRound' else MODES[rnd]
        rows.append(f'    floatText :t x: {zstr(x)} round: {zstr(md)} prec: {p} '
                    f'format: math.floatformat.{FORMATS[f]} digits: {d} want: {zstr(want)}')

    # decimal_test.go TestDecimalInit: x * 2^shift spelled exactly
    i = dc.index('func TestDecimalInit')
    seg = uncomment(dc[i:dc.index('func TestDecimalRounding')])
    for m in re.finditer(r'\{(\d+), (-?\d+), "([^"]*)"\}', seg):
        x, sh, want = m.groups()
        fd = len(want.split('.')[1]) if '.' in want else 0
        rows.append(f'    decimalInit :t x: {x} shift: {sh} digits: {fd} want: {zstr(want)}')

    # TestDecimalRounding: round, to nearest even at n digits, is the exponent
    # form with n - 1 digits after the point
    i = dc.index('func TestDecimalRounding')
    seg = uncomment(dc[i:dc.index('var sink')])
    for m in re.finditer(r'\{(\d+), (\d+), "(\d+)", "(\d+)", "(\d+)"\}', seg):
        x, n, _, even, _ = m.groups()
        n = int(n)
        if n == 0:
            continue
        if even == '0':
            want = '0' + ('.' + '0' * (n - 1) if n > 1 else '') + 'e+00'
        else:
            dg = (even + '0' * n)[:n]
            ex = len(even) - 1
            want = dg[0] + ('.' + dg[1:] if n > 1 else '') + f'e+{ex:02d}'
        rows.append(f'    decimalRounding :t x: {x} n: {n} want: {zstr(want)}')
    return rows


COMMON = '''# the failures, and the checks run
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

# mk -- Go's makeFloat: the text read at 1000 bits, nearest even
mk: function {text: StringView} out math.BigFloat is {
    return (mkp :text base: 0 prec: 1000)
}

# mkp -- the text read in `base` at `prec` bits, nearest even
mkp: function {text: StringView base: u64 prec: u32} out math.BigFloat is {
    r: math.BigFloat.parse :text :base :prec
    match r case ok then return r.take case err then panic msg: "bad test text"
}

# accText -- an accuracy's name, for a failure's message
accText: function {a: math.accuracy} out StringView is {
    match a case below then return "Below" case exact then return "Exact" case above then return "Above"
}

# alike -- equal, and with the same sign: a zero's sign counts
alike: function {x: math.BigFloat.view y: math.BigFloat.view} out bool is {
    return (x == y) and (x.negative == y.negative)
}

# f64Of -- the f64 a bit pattern encodes, built by scaling
f64Of: function {bits: u64} out f64 is {
    e: (bits >> 52) & 2047
    m: bits & 4503599627370495
    v: f64
    if e == 2047 then {
        z: f64
        v = 1.0 / z
    } else if e == 0 then {
        v = m.f64.orPanic.scaleB n: -1074
    } else {
        v = (m | 4503599627370496).f64.orPanic.scaleB n: (e.i64.orPanic - 1075).i32.orPanic
    }
    if (bits >> 63) == 1 then v = v.copySign y: -1.0
    return v
}
'''

FLOAT_HEADER = '''# math_bigfloat.z -- Go's float_test.go, bits_test.go and sqrt_test.go for
# BigFloat, generated by work/zerolang/tools/floattables.py from the Go
# sources. Go's float64 operands are bit patterns rounded once by an exact
# oracle, and the Float32/Float64 tables are checked against it. Go's Bits
# type, an independent sum-of-powers-of-two representation, checks the four
# operators at every precision and mode Go uses. A NaN result panics, which a
# run cannot recover from, so those cases are left out.

''' + COMMON + '''
# apply -- x op y, op 0 to 3 for + - * /, at `prec` bits by `mode`
apply: function {
    op: i64
    x: math.BigFloat.view
    y: math.BigFloat.view
    prec: u32
    mode: math.roundingmode
} out math.BigFloat is {
    if op == 0 then return (math.BigFloat.sum :x :y :prec :mode)
    if op == 1 then return (math.BigFloat.difference :x :y :prec :mode)
    if op == 2 then return (math.BigFloat.product :x :y :prec :mode)
    return (math.BigFloat.quotient :x :y :prec :mode)
}

# zeroValue -- Go's TestFloatZeroValue: a zero of precision 0 for 0, else the
# integer at 64 bits, as either operand and as the result's precision
zeroValue: function {t: Tally.borrow z: i64 x: i64 y: i64 want: i64 op: i64} is {
    a: math.BigFloat.zero
    if x != 0 then a = math.BigFloat.fromI64 from: x
    b: math.BigFloat.zero
    if y != 0 then b = math.BigFloat.fromI64 from: y
    p: 0.u32
    if z != 0 then p = 64
    r: apply :op x: a y: b prec: p mode: math.roundingmode.toNearestEven
    got: 0
    if r.isInf.not then got = r.i64.orPanic
    truth :t what: "zero value" ok: got == want
}

# setPrec -- x rounded to prec bits: its precision, text and accuracy
setPrec: function {t: Tally.borrow x: StringView prec: u32 want: StringView acc: math.accuracy} is {
    z: (mk text: x).withPrec :prec
    truth :t what: x ok: z.prec == prec
    same :t what: x got: z.string :want
    same :t what: x got: (accText a: z.acc).string want: (accText a: acc)
}

# minPrec -- the fewest bits that hold x at 100 bits
minPrec: function {t: Tally.borrow x: StringView want: u64} is {
    truth :t what: x ok: ((mk text: x).withPrec prec: 100).minPrec == want
}

# signOf -- the sign
signOf: function {t: Tally.borrow x: StringView want: i32} is {
    truth :t what: x ok: (mk text: x).sign == want
}

# mantExp -- x as mantissa and exponent
mantExp: function {t: Tally.borrow x: StringView mant: StringView exp: i64} is {
    v: mk text: x
    truth :t what: x ok: (alike x: v.mantissa y: (mk text: mant)) and (v.exponent == exp)
}

# setMantExp -- frac * 2^exp, and the inverse property
setMantExp: function {t: Tally.borrow frac: StringView exp: i64 want: StringView} is {
    w: mk text: want
    z: (mk text: frac).scaleB n: exp
    truth :t what: frac ok: (alike x: z y: w)
    back: w.mantissa.scaleB n: w.exponent
    truth :t what: want ok: back == w
}

# predicates -- the sign bit, the sign and isInf
predicates: function {t: Tally.borrow x: StringView sign: i32 signbit: bool inf: bool} is {
    v: mk text: x
    truth :t what: x ok: (v.negative == signbit) and (v.sign == sign) and (v.isInf == inf)
}

# isInt -- whether x is an integer
isInt: function {t: Tally.borrow x: StringView want: bool} is {
    truth :t what: x ok: (mk text: x).isInt == want
}

# roundCase -- Go's testFloatRound: x rounded to prec bits by mode is r, with
# the accuracy r has against x, whether rounded after or while setting, and
# rounding again changes nothing
roundCase: function {t: Tally.borrow x: i64 r: i64 prec: u32 mode: math.roundingmode} is {
    a: math.accuracy.exact
    if r < x then a = math.accuracy.below
    if r > x then a = math.accuracy.above
    f: (math.BigFloat.fromI64 from: x :mode).withPrec :prec
    ok: (f.i64.orPanic == r) and (f.prec == prec) and ((accText a: f.acc) == (accText :a))
    truth :t what: "round \\{x} to \\{prec}".stringView :ok
    g: math.BigFloat.fromI64 from: x :prec :mode
    truth :t what: "round symmetric \\{x}".stringView ok: (alike x: g y: f)
    h: f.withPrec :prec
    truth :t what: "round idempotent \\{x}".stringView ok: (alike x: h y: f)
}

# round24 -- a float64 at 24 bits converts to the float32 IEEE gives it
round24: function {t: Tally.borrow x: i64 want: u32} is {
    f: math.BigFloat from: x.f64.orPanic prec: 24
    truth :t what: "round24 \\{x}".stringView ok: f.roundF32.bits == want
}

# setFloat64 -- an f64 reads back as itself, exactly
setFloat64: function {t: Tally.borrow bits: u64} is {
    v: f64Of :bits
    f: math.BigFloat from: v
    truth :t what: "setFloat64 \\{bits}".stringView ok: f.roundF64.bits == bits
    match f.f64 case ok then truth :t what: "f64 exact \\{bits}".stringView ok: true case err then truth :t what: "f64 exact \\{bits}".stringView ok: false
}

# setInt -- a BigInt at its own precision, at least 64 bits
setInt: function {t: Tally.borrow text: StringView} is {
    r: math.BigInt.parse :text base: 0
    x: math.BigInt from: 0
    match r case ok then x = r.take case err then panic msg: "bad test text"
    n: x.bitLength
    if n < 64 then n = 64
    f: math.BigFloat.fromInt from: x
    truth :t what: text ok: f.prec.u64 == n
    same :t what: "setInt" got: (f.text format: math.floatformat.general digits: 100) want: text
}

# setRat -- a BigRat at its own precision, and at 1000 bits spelled back
setRat: function {t: Tally.borrow text: StringView} is {
    r: math.BigRat.parse :text
    x: math.BigRat from: 0
    match r case ok then x = r.take case err then panic msg: "bad test text"
    n: x.num.bitLength
    if x.den.bitLength > n then n = x.den.bitLength
    if n < 64 then n = 64
    f1: math.BigFloat.fromRat from: x
    truth :t what: text ok: f1.prec.u64 == n
    f2: math.BigFloat.fromRat from: x prec: 1000
    same :t what: "setRat" got: (f2.text format: math.floatformat.general digits: 100) want: text
}

# setInf -- an infinity keeps its precision
setInf: function {t: Tally.borrow negative: bool prec: u32 want: StringView} is {
    f: math.BigFloat.inf :negative :prec
    same :t what: "setInf" got: f.string :want
    truth :t what: want ok: f.prec == prec
}

# kindOf -- "ok", "outOfRange" or "precisionLoss"
kindOf: function {e: converror} out StringView is {
    match e case outOfRange then return "outOfRange" case precisionLoss then return "precisionLoss"
}

# accOf -- how the clamped result r compares to x, as Go reports it
accOf: function {r: math.BigFloat.view x: math.BigFloat.view} out math.accuracy is {
    c: r.compare rhs: x
    if c < 0 then return math.accuracy.below
    if c > 0 then return math.accuracy.above
    return math.accuracy.exact
}

# toU64 -- Go's Uint64 table: the exact conversion, the truncation where it
# fits, and the clamped result's accuracy
toU64: function {t: Tally.borrow x: StringView want: u64 acc: math.accuracy kind: StringView} is {
    v: mk text: x
    r: v.u64
    match r case ok then same :t what: x got: "ok".string want: kind case err then same :t what: x got: (kindOf e: r).string want: kind
    truth :t what: x ok: (accText a: (accOf r: (math.BigFloat.fromU64 from: want) x: v)) == (accText a: acc)
    tr: v.truncInt
    match tr case some then {
        u: tr.u64
        match u case ok then truth :t what: x ok: u == want case err then null
    } case none then null
}

# toI64 -- Go's Int64 table, likewise
toI64: function {t: Tally.borrow x: StringView want: i64 acc: math.accuracy kind: StringView} is {
    v: mk text: x
    r: v.i64
    match r case ok then same :t what: x got: "ok".string want: kind case err then same :t what: x got: (kindOf e: r).string want: kind
    truth :t what: x ok: (accText a: (accOf r: (math.BigFloat.fromI64 from: want) x: v)) == (accText a: acc)
    tr: v.truncInt
    match tr case some then {
        u: tr.i64
        match u case ok then truth :t what: x ok: u == want case err then null
    } case none then null
}

# toF32 -- the nearest f32 is the oracle's, exact as the table says, and reads
# back exactly
toF32: function {t: Tally.borrow x: StringView bits: u32 acc: math.accuracy} is {
    v: mk text: x
    f: v.roundF32
    truth :t what: x ok: f.bits == bits
    exact: false
    match v.f32 case ok then exact = true case err then null
    truth :t what: x ok: exact == acc.exact
    back: math.BigFloat from: f.f64
    truth :t what: x ok: back.roundF32.bits == bits
}

# toF64 -- the nearest f64 likewise
toF64: function {t: Tally.borrow x: StringView bits: u64 acc: math.accuracy} is {
    v: mk text: x
    f: v.roundF64
    truth :t what: x ok: f.bits == bits
    exact: false
    match v.f64 case ok then exact = true case err then null
    truth :t what: x ok: exact == acc.exact
    back: math.BigFloat from: f
    truth :t what: x ok: back.roundF64.bits == bits
}

# toInt -- truncation toward zero, none for an infinity, and its accuracy
toInt: function {t: Tally.borrow x: StringView want: StringView acc: math.accuracy} is {
    v: mk text: x
    r: v.truncInt
    match r case some then {
        same :t what: x got: r.string :want
        truth :t what: x ok: (accText a: (accOf r: (math.BigFloat.fromInt from: r) x: v)) == (accText a: acc)
    } case none then {
        same :t what: x got: "nil".string :want
    }
}

# toRat -- x at 64 bits exactly as a fraction, and back
toRat: function {t: Tally.borrow x: StringView want: StringView acc: math.accuracy} is {
    v: (mk text: x).withPrec prec: 64
    r: v.rat
    match r case some then {
        same :t what: x got: r.fraction :want
        truth :t what: x ok: acc.exact
        truth :t what: x ok: (math.BigFloat.fromRat from: r prec: 64) == v
    } case none then {
        same :t what: x got: "nil".string :want
    }
}

# absNeg -- abs and neg of x and -x
absNeg: function {t: Tally.borrow x: StringView} is {
    p: mk text: x
    n: mk text: "-\\{x}".stringView
    truth :t what: x ok: (alike x: p.abs y: p)
    truth :t what: x ok: (alike x: n.abs y: p)
    truth :t what: x ok: (alike x: p.neg y: n)
    truth :t what: x ok: (alike x: n.neg y: p)
}

# mul64 -- Go's TestFloatMul64: at 53 bits a product and a quotient are the
# f64 ones, for every sign and order
mul64: function {t: Tally.borrow x: u64 y: u64} is {
    for each i: 8.u64.times loop {
        x0: f64Of bits: x
        y0: f64Of bits: y
        if (i & 1) != 0 then x0 = x0 * -1.0
        if (i & 2) != 0 then y0 = y0 * -1.0
        if (i & 4) != 0 then x0 swap y0
        a: math.BigFloat from: x0
        b: math.BigFloat from: y0
        z: math.BigFloat.product x: a y: b prec: 53 mode: math.roundingmode.toNearestEven
        want: x0 * y0
        truth :t what: "mul64" ok: z.roundF64 == want
        if y0 != 0.0 then {
            q: math.BigFloat.quotient x: z y: b prec: 53 mode: math.roundingmode.toNearestEven
            truth :t what: "quo64" ok: q.roundF64 == (want / y0)
        }
    }
}

# overflow -- Go's TestFloatArithmeticOverflow
overflow: function {
    t: Tally.borrow
    prec: u32
    mode: math.roundingmode
    op: i64
    x: StringView
    y: StringView
    want: StringView
    acc: math.accuracy
} is {
    z: apply :op x: (mk text: x) y: (mk text: y) :prec :mode
    same :t what: x got: (z.text format: math.floatformat.hexFraction digits: 0) :want
    same :t what: x got: (accText a: z.acc).string want: (accText a: acc)
}

# arithRounding -- Go's TestFloatArithmeticRounding: the sign is set before
# rounding
arithRounding: function {t: Tally.borrow mode: math.roundingmode prec: u32 x: i64 y: i64 want: i64 op: i64} is {
    z: apply :op x: (math.BigFloat.fromI64 from: x) y: (math.BigFloat.fromI64 from: y) :prec :mode
    r: z.i64
    match r case ok then truth :t what: "arith rounding \\{x}".stringView ok: r == want case err then truth :t what: "arith rounding \\{x}".stringView ok: false
}

# Bits -- Go's bits_test.go Bits: a value as a sum of powers of two, 2^b for
# each b, repeats allowed -- an independent representation the operators are
# checked against
Bits: class {
    b: ListVal i64
}

# bitsOf -- a Bits of up to five powers
bitsOf: function {n: i64 b0: 0 b1: 0 b2: 0 b3: 0 b4: 0} out Bits is {
    r: Bits b: (ListVal i64)
    if n > 0 then r.b.append b0
    if n > 1 then r.b.append b1
    if n > 2 then r.b.append b2
    if n > 3 then r.b.append b3
    if n > 4 then r.b.append b4
    return r
}

# bitsAdd -- x + y: both lists of powers
bitsAdd: function {x: Bits.view y: Bits.view} out Bits is {
    r: Bits b: x.b.copy
    for each v: y.b.iterate loop r.b.append v
    return r
}

# bitsMul -- x * y: every pairwise sum of powers
bitsMul: function {x: Bits.view y: Bits.view} out Bits is {
    r: Bits b: (ListVal i64)
    for each a: x.b.iterate loop {
        for each c: y.b.iterate loop r.b.append a + c
    }
    return r
}

# bitsNorm -- each power at most once, sorted: equal powers carry upward
bitsNorm: function {x: Bits.view} out Bits is {
    set: ListVal i64
    for each v: x.b.iterate loop {
        b: v
        for (set.contains item: b) loop {
            keep: ListVal i64
            for each w: set.iterate loop {
                if w != b then keep.append w
            }
            set = keep.take
            b = b + 1
        }
        set.append b
    }
    set.sort
    return (Bits b: set.take)
}

# bitsFloat -- Go's Bits.Float: the value exactly, at the smallest precision
# that holds it (as fromInt picks it)
bitsFloat: function {x: Bits.view} out math.BigFloat is {
    if x.b.length == 0 then return math.BigFloat.zero
    lo: x.b.get i: 0
    for each v: x.b.iterate loop {
        if v < lo then lo = v
    }
    n: math.BigInt from: 0
    for each v: x.b.iterate loop {
        k: (v - lo).u64.orPanic
        for (n.bit i: k) != 0 loop {
            n = n.setBit i: k b: 0
            k = k + 1
        }
        n = n.setBit i: k b: 1
    }
    return ((math.BigFloat.fromInt from: n).scaleB n: lo)
}

# bitsRound -- Go's Bits.round: x rounded to prec bits by mode, worked out on
# the powers: the bits above the rounding bit, plus one at the lowest kept
# place when the mode rounds away
bitsRound: function {x: Bits.view prec: u32 mode: math.roundingmode} out math.BigFloat is {
    y: bitsNorm :x
    if y.b.length == 0 then return math.BigFloat.zero
    lo: y.b.get i: 0
    hi: y.b.get i: y.b.length - 1
    if prec.i64 >= ((hi + 1) - lo) then return (bitsFloat x: y)
    r: hi - prec.i64
    bit0: false
    rbit: false
    sbit: false
    z: Bits b: (ListVal i64)
    for each b: y.b.iterate loop {
        if b == r then {
            rbit = true
        } else if b < r then {
            sbit = true
        } else {
            if b == (r + 1) then bit0 = true
            z.b.append b
        }
    }
    f: bitsFloat x: z
    away: mode.awayFromZero or (mode.toNearestEven and rbit and (sbit or bit0))
    if away then {
        g: (f.withMode mode: math.roundingmode.toZero).withPrec :prec
        g.add x: (bitsFloat x: (bitsOf n: 1 b0: r + 1))
        return g
    }
    return f
}

# precs -- Go's precList
precs: function out (ListVal u32) is {
    p: ListVal u32
    p.append 1\n    p.append 2\n    p.append 5\n    p.append 8\n    p.append 10\n    p.append 16\n    p.append 23\n    p.append 24\n    p.append 32\n    p.append 50\n    p.append 53\n    p.append 64\n    p.append 100\n    p.append 128\n    p.append 500\n    p.append 511\n    p.append 512\n    p.append 513\n    p.append 1000\n    p.append 10000\n    return p
}

# threeModes -- the modes Go's operator tests run in
threeModes: function out (ListVal math.roundingmode) is {
    modes: ListVal math.roundingmode
    modes.append math.roundingmode.toZero
    modes.append math.roundingmode.toNearestEven
    modes.append math.roundingmode.awayFromZero
    return modes
}

# bitsList -- Go's bitsList
bitsList: function out (List Bits) is {
    l: List Bits
    l.append (bitsOf n: 0)
    l.append (bitsOf n: 1 b0: 0)
    l.append (bitsOf n: 1 b0: 1)
    l.append (bitsOf n: 1 b0: -1)
    l.append (bitsOf n: 1 b0: 10)
    l.append (bitsOf n: 1 b0: -10)
    l.append (bitsOf n: 3 b0: 100 b1: 10 b2: 1)
    l.append (bitsOf n: 4 b0: 0 b1: -1 b2: -2 b3: -10)
    return l
}

# bitsArith -- Go's TestFloatAdd and TestFloatMul: x + y, z - x, x * y and
# z / x at every precision in three modes, against the rounded powers
bitsArith: function {t: Tally.borrow} is {
    pl: precs
    bl: bitsList
    modes: threeModes
    for each xb: bl.iterate loop {
        for each yb: bl.iterate loop {
            x: bitsFloat x: xb
            y: bitsFloat x: yb
            sb: bitsAdd x: xb y: yb
            s: bitsFloat x: sb
            pb: bitsMul x: xb y: yb
            p: bitsFloat x: pb
            for each m: modes.iterate loop {
                for each prec: pl.iterate loop {
                    truth :t what: "add" ok: (math.BigFloat.sum :x :y :prec mode: m) == (bitsRound x: sb :prec mode: m)
                    truth :t what: "sub" ok: (math.BigFloat.difference x: s y: x :prec mode: m) == (bitsRound x: yb :prec mode: m)
                    truth :t what: "mul" ok: (math.BigFloat.product :x :y :prec mode: m) == (bitsRound x: pb :prec mode: m)
                    if x.sign != 0 then {
                        truth :t what: "quo" ok: (math.BigFloat.quotient x: p y: x :prec mode: m) == (bitsRound x: yb :prec mode: m)
                    }
                }
            }
        }
    }
}

# fromBits -- Go's TestFromBits
fromBits: function {t: Tally.borrow bits: Bits.view want: StringView} is {
    same :t what: "fromBits" got: ((bitsFloat x: bits).text format: math.floatformat.hexFraction digits: 0) :want
}

# setBitsRounding -- Go's rounding loops in TestFloatSetUint64, SetInt64 and
# SetFloat64: toward zero at each precision drops the low bits
setBitsRounding: function {t: Tally.borrow} is {
    x: 9756277979052589857.u64
    for each prec: (1.u64.upto to: 64) loop {
        f: math.BigFloat.fromU64 from: x prec: prec.u32.orPanic mode: math.roundingmode.toZero
        mask: 18446744073709551615.u64
        if prec < 64 then mask = ((1.u64 << (64.u64 - prec)) - 1).not
        truth :t what: "setU64 \\{prec}".stringView ok: f.u64.orPanic == (x & mask)
    }
    y: 8526495040805286416
    for each prec: (1.u64.upto to: 63) loop {
        f: math.BigFloat.fromI64 from: y prec: prec.u32.orPanic mode: math.roundingmode.toZero
        mask: ((1 << (63.u64 - prec).i64.orPanic) - 1).not
        truth :t what: "setI64 \\{prec}".stringView ok: f.i64.orPanic == (y & mask)
    }
    z: 2380629469148696.u64
    for each prec: (1.u64.upto to: 52) loop {
        f: math.BigFloat from: z.f64.orPanic prec: prec.u32.orPanic mode: math.roundingmode.toZero
        mask: ((1.u64 << (52.u64 - prec)) - 1).not
        truth :t what: "setF64 \\{prec}".stringView ok: f.roundF64 == (z & mask).f64.orPanic
    }
    vals: ListVal i64
    vals.append 0
    vals.append 1
    vals.append 2
    vals.append 10
    vals.append 100
    vals.append 4294967295
    vals.append 4294967296
    for each v: vals.iterate loop {
        truth :t what: "setU64 \\{v}".stringView ok: (math.BigFloat.fromU64 from: v.u64.orPanic).u64.orPanic == v.u64.orPanic
        truth :t what: "setI64 \\{v}".stringView ok: (math.BigFloat.fromI64 from: v).i64.orPanic == v
        truth :t what: "setI64 -\\{v}".stringView ok: (math.BigFloat.fromI64 from: 0 - v).i64.orPanic == (0 - v)
    }
    m: 18446744073709551615.u64
    truth :t what: "setU64 max" ok: (math.BigFloat.fromU64 from: m).u64.orPanic == m
    n: 9223372036854775807
    truth :t what: "setI64 max" ok: (math.BigFloat.fromI64 from: n).i64.orPanic == n
    truth :t what: "setI64 -max" ok: (math.BigFloat.fromI64 from: 0 - n).i64.orPanic == (0 - n)
}

# incTest -- Go's TestFloatInc: adding 1 ten times counts to 10
incTest: function {t: Tally.borrow} is {
    one: math.BigFloat.fromI64 from: 1
    ten: math.BigFloat.fromI64 from: 10
    for each prec: precs.iterate loop {
        if prec >= 4 then {
            x: math.BigFloat.zero :prec
            for each k: 10.u64.times loop {
                k.drop
                x.add x: one
            }
            truth :t what: "inc \\{prec}".stringView ok: x == ten
        }
    }
}

# addRoundZero -- Go's TestFloatAddRoundZero: x + -x and x - x are +0, -0
# toward negative infinity
addRoundZero: function {t: Tally.borrow} is {
    modes: ListVal math.roundingmode
    modes.append math.roundingmode.toNearestEven
    modes.append math.roundingmode.toNearestAway
    modes.append math.roundingmode.toZero
    modes.append math.roundingmode.awayFromZero
    modes.append math.roundingmode.toPositiveInf
    modes.append math.roundingmode.toNegativeInf
    x: math.BigFloat from: 5.0
    y: x.neg
    for each m: modes.iterate loop {
        s: math.BigFloat.sum :x :y prec: 0 mode: m
        truth :t what: "add round zero" ok: (s.sign == 0) and (s.negative == m.toNegativeInf)
        d: math.BigFloat.difference :x y: x prec: 0 mode: m
        truth :t what: "sub round zero" ok: (d.sign == 0) and (d.negative == m.toNegativeInf)
    }
}

# addFloat -- Go's TestFloatAdd32 and TestFloatAdd64: at 24 and 53 bits a sum
# and a difference are the f32 and f64 ones
addFloat: function {t: Tally.borrow} is {
    for each d: (0.upto to: 16) loop {
        for each i: 2.u64.times loop {
            x0: 67108848.0.f32
            y0: d.f32.orPanic
            if i == 1 then x0 swap y0
            x: math.BigFloat from: x0.f64
            y: math.BigFloat from: y0.f64
            z: math.BigFloat.sum :x :y prec: 24 mode: math.roundingmode.toNearestEven
            want: y0 + x0
            truth :t what: "add32 \\{d}".stringView ok: (z.roundF32 == want) and (z.f32).ok
            z2: math.BigFloat.difference x: z :y prec: 24 mode: math.roundingmode.toNearestEven
            truth :t what: "sub32 \\{d}".stringView ok: (z2.roundF32 == (want - y0)) and (z2.f32).ok
        }
    }
    for each d: (0.upto to: 16) loop {
        for each i: 2.u64.times loop {
            x0: 36028797018963952.0
            y0: d.f64.orPanic
            if i == 1 then x0 swap y0
            x: math.BigFloat from: x0
            y: math.BigFloat from: y0
            z: math.BigFloat.sum :x :y prec: 53 mode: math.roundingmode.toNearestEven
            want: x0 + y0
            truth :t what: "add64 \\{d}".stringView ok: (z.roundF64 == want) and (z.f64).ok
            z2: math.BigFloat.difference x: z :y prec: 53 mode: math.roundingmode.toNearestEven
            truth :t what: "sub64 \\{d}".stringView ok: (z2.roundF64 == (want - y0)) and (z2.f64).ok
        }
    }
}

# inPlace -- Go's TestIssue20490 as the in-place forms: b.sub and b.add agree
# with the operators
inPlace: function {t: Tally.borrow} is {
    for each i: 4.u64.times loop {
        v: 4.0
        w: 1.0
        if (i & 1) != 0 then v = -4.0
        if (i & 2) != 0 then w = -1.0
        {
            a: math.BigFloat from: v
            b: math.BigFloat from: w
            d: a - b
            s: a + b
            c: a.copy
            c.sub x: b
            truth :t what: "in-place sub" ok: c == d
            c2: a.copy
            c2.add x: b
            truth :t what: "in-place add" ok: c2 == s
        }
    }
}

# issue6866 -- Go's TestIssue6866: 2 + 1/3 * -6 and 2 - 1/3 * 6 are both 0
issue6866: function {t: Tally.borrow} is {
    for each prec: precs.iterate loop {
        tn: math.roundingmode.toNearestEven
        two: math.BigFloat.fromI64 from: 2 :prec
        one: math.BigFloat.fromI64 from: 1 :prec
        three: math.BigFloat.fromI64 from: 3 :prec
        msix: math.BigFloat.fromI64 from: -6 :prec
        psix: math.BigFloat.fromI64 from: 6 :prec
        p: math.BigFloat.quotient x: one y: three :prec mode: tn
        p1: math.BigFloat.product x: p y: msix :prec mode: tn
        z1: math.BigFloat.sum x: two y: p1 :prec mode: tn
        p2: math.BigFloat.product x: p y: psix :prec mode: tn
        z2: math.BigFloat.difference x: two y: p2 :prec mode: tn
        truth :t what: "issue6866 \\{prec}".stringView ok: (z1 == z2) and (z1.sign == 0) and (z2.sign == 0)
    }
}

# quoTest -- Go's TestFloatQuo: x = z * y exactly, then x / y at precisions
# around z's is z rounded
quoTest: function {t: Tally.borrow} is {
    preci: 200
    precf: 20
    y: math.BigFloat from: 3.14159265358979323e123
    for each i: (0.upto to: 7) loop {
        bits: Bits b: (ListVal i64)
        bits.b.append preci - 1
        if (i & 3) != 0 then bits.b.append 0
        if (i & 2) != 0 then bits.b.append -1
        if (i & 1) != 0 then bits.b.append 0 - precf
        z: bitsFloat x: bits
        x: math.BigFloat.product x: z :y prec: z.prec + y.prec mode: math.roundingmode.toZero
        truth :t what: "quo exact" ok: x.acc.exact
        lo: -5
        for each m: threeModes.iterate loop {
            for each d: (lo.upto to: 4) loop {
                prec: (preci + d).u32.orPanic
                got: math.BigFloat.quotient :x :y :prec mode: m
                truth :t what: "quo \\{i} \\{prec}".stringView ok: got == (bitsRound x: bits :prec mode: m)
            }
        }
    }
}

# quoSmoke -- Go's TestFloatQuoSmoke: x / y for small integers, the operands at
# varied precisions, is the f64 quotient exactly
quoSmoke: function {t: Tally.borrow} is {
    n: 10
    for each x: ((0 - n).upto to: n) loop {
        for each y: ((0 - n).upto to: n - 1) loop {
            if y != 0 then {
                a: x.f64.orPanic
                b: y.f64.orPanic
                c: a / b
                lo: -3
                for each ad: (lo.upto to: 3) loop {
                    for each bd: (lo.upto to: 3) loop {
                        av: math.BigFloat from: a prec: (13 + ad).u32.orPanic
                        bv: math.BigFloat from: b prec: (13 + bd).u32.orPanic
                        q: math.BigFloat.quotient x: av y: bv prec: 53 mode: math.roundingmode.toNearestEven
                        ok: false
                        r: q.f64
                        match r case ok then ok = r == c case err then null
                        truth :t what: "quo smoke \\{x}/\\{y}".stringView :ok
                    }
                }
            }
        }
    }
}

# specials -- Go's float specials: -Inf, -2.71828, -1, -0, 0, 1, 2.71828,
# +Inf
specials: function out (ListVal f64) is {
    z: f64
    inf: 1.0 / z
    l: ListVal f64
    l.append 0.0 - inf
    l.append -2.71828
    l.append -1.0
    l.append (0.0.copySign y: -1.0)
    l.append 0.0
    l.append 1.0
    l.append 2.71828
    l.append inf
    return l
}

# specialValues -- Go's TestFloatArithmeticSpecialValues and
# TestFloatCmpSpecialValues: the operators and compare on zeros, ones and
# infinities agree with f64 arithmetic, sign of zero included; the NaN cases
# panic and are left out
specialValues: function {t: Tally.borrow} is {
    args: specials
    for each x: args.iterate loop {
        xx: math.BigFloat from: x
        truth :t what: "special" ok: xx.roundF64.bits == x.bits
        for each y: args.iterate loop {
            yy: math.BigFloat from: y
            for each op: (0.upto to: 3) loop {
                z: x + y
                if op == 1 then z = x - y
                if op == 2 then z = x * y
                if op == 3 then z = x / y
                if z.isNaN.not then {
                    got: apply :op x: xx y: yy prec: 0 mode: math.roundingmode.toNearestEven
                    truth :t what: "special \\{x} \\{op} \\{y}".stringView ok: (alike x: got y: (math.BigFloat from: z))
                }
            }
            want: 0.i32
            if x < y then want = -1
            if x > y then want = 1
            truth :t what: "cmp \\{x} \\{y}".stringView ok: (xx.compare rhs: yy) == want
        }
    }
}

# ieeeCase -- the binary16 and binary128 patterns nearest x are the oracle's,
# and each reads back as the value it encodes
ieeeCase: function {
    t: Tally.borrow
    x: StringView
    bits16: StringView
    bits128: StringView
    rat16: StringView
    rat128: StringView
} is {
    # the exact value as a BigRat, rounded once: a rational has no -0
    if rat16.length > 0 then {
        q: math.BigRat.parse text: x
        match q case ok then {
            same :t what: x got: ((q.ieeeBits mbits: 10 ebits: 5).text base: 16) want: (rat16.substring from: 2 to: rat16.length)
            same :t what: x got: ((q.ieeeBits mbits: 112 ebits: 15).text base: 16) want: (rat128.substring from: 2 to: rat128.length)
        } case err then {
            truth :t what: x ok: false
        }
    }
    v: mk text: x
    same :t what: x got: ((v.ieeeBits mbits: 10 ebits: 5).text base: 16) want: (bits16.substring from: 2 to: bits16.length)
    same :t what: x got: ((v.ieeeBits mbits: 112 ebits: 15).text base: 16) want: (bits128.substring from: 2 to: bits128.length)
    for each k: 2.u64.times loop {
        mb: 10.u64
        eb: 5.u64
        if k == 1 then {
            mb = 112
            eb = 15
        }
        b: v.ieeeBits mbits: mb ebits: eb
        back: math.BigFloat.fromIEEEBits bits: b mbits: mb ebits: eb
        match back case some then {
            truth :t what: x ok: (back.ieeeBits mbits: mb ebits: eb) == b
            truth :t what: x ok: back.negative == v.negative
        } case none then {
            truth :t what: x ok: false
        }
    }
}

# sqrtCase -- Go's TestFloatSqrt: the root at each precision is the reference
# value read at that precision, and its square is within the error bound
sqrtCase: function {t: Tally.borrow x: StringView want: StringView} is {
    pl: ListVal u32
    pl.append 24\n    pl.append 53\n    pl.append 64\n    pl.append 65\n    pl.append 100\n    pl.append 128\n    pl.append 129\n    pl.append 200\n    pl.append 256\n    pl.append 400\n    pl.append 600\n    pl.append 800\n    pl.append 1000\n    for each p: pl.iterate loop {
        prec: p.i64
        xv: mkp text: x base: 10 prec: p
        got: xv.sqrt
        w: mkp text: want base: 10 prec: p
        truth :t what: "sqrt \\{x} \\{prec}".stringView ok: got == w
        sq: math.BigFloat.product x: got y: got prec: p + 32 mode: math.roundingmode.toNearestEven
        diff: sq - xv
        err: diff.abs.withPrec prec: p
        one: math.BigFloat.fromI64 from: 1 prec: p
        maxErr: (one.scaleB n: 1 - prec) * got
        truth :t what: "sqrt error \\{x} \\{prec}".stringView ok: err < maxErr
    }
}

# sqrt64 -- Go's TestFloatSqrt64: at 53 bits the root is libm's correctly
# rounded one, for pseudo-random values in [0, 1)
sqrt64: function {t: Tally.borrow} is {
    s: 88172645463325252.u64
    for each k: 2000.u64.times loop {
        k.drop
        s = s + 11400714819323198485
        v: s
        v = (v ^ (v >> 30)) * 13787848793156543929
        v = (v ^ (v >> 27)) * 10723151780598845931
        v = v ^ (v >> 31)
        r: (v >> 11).f64.orPanic.scaleB n: -53
        got: (math.BigFloat from: r).sqrt
        truth :t what: "sqrt64 \\{r}".stringView ok: got.roundF64 == r.sqrt
    }
}

# sqrtSpecial -- Go's TestFloatSqrtSpecial: +0, -0 and +Inf are their own
# roots
sqrtSpecial: function {t: Tally.borrow} is {
    z: f64
    vals: ListVal f64
    vals.append 0.0
    vals.append (0.0.copySign y: -1.0)
    vals.append 1.0 / z
    for each v: vals.iterate loop {
        f: math.BigFloat from: v
        truth :t what: "sqrt special" ok: (alike x: f.sqrt y: f)
    }
}

main: function is {
    t: Tally checks: 0 bad: 0
'''

FLOAT_FOOTER = '''    setBitsRounding :t
    incTest :t
    bitsArith :t
    addRoundZero :t
    addFloat :t
    inPlace :t
    issue6866 :t
    quoTest :t
    quoSmoke :t
    specialValues :t
    sqrt64 :t
    sqrtSpecial :t
    if t.bad == 0 then print "ok \\{t.checks} checks" else print "\\{t.bad} of \\{t.checks} checks failed"
}
'''

TEXT_HEADER = '''# math_bigfloat_text.z -- Go's floatconv_test.go and decimal_test.go for
# BigFloat's parse and text, generated by work/zerolang/tools/floattables.py
# from the Go sources. Go's float64 values are bit patterns rounded once by an
# exact oracle. The decimal type's own tests are restated through text: the
# exact expansion of x * 2^shift in fixed format, and rounding to nearest even
# at n digits as the exponent form with n - 1 digits.

''' + COMMON + '''
# readsFloat -- Go's TestFloatSetFloat64String: the text at 53 bits is the f64,
# sign of zero included, or is refused
readsFloat: function {t: Tally.borrow text: StringView ok: bool bits: u64} is {
    r: math.BigFloat.parse :text prec: 53
    match r case ok then {
        truth :t what: text :ok
        if ok then truth :t what: text ok: (alike x: r y: (math.BigFloat from: (f64Of :bits)))
    } case err then {
        truth :t what: text ok: ok.not
    }
}

# f64Text -- Go's TestFloat64Text: an f64 at the precision it has, spelled
f64Text: function {
    t: Tally.borrow
    bits: u64
    prec: u32
    format: math.floatformat
    digits: i64
    want: StringView
} is {
    f: math.BigFloat from: (f64Of :bits) :prec
    same :t what: "text" got: (f.text :format :digits) :want
}

# floatText -- Go's TestFloatText: the text read at prec bits, its mode set
# unless "none", spelled
floatText: function {
    t: Tally.borrow
    x: StringView
    round: StringView
    prec: u32
    format: math.floatformat
    digits: i64
    want: StringView
} is {
    f: mkp text: x base: 0 :prec
    if round == "toNearestEven" then f = f.withMode mode: math.roundingmode.toNearestEven
    if round == "toNearestAway" then f = f.withMode mode: math.roundingmode.toNearestAway
    if round == "toZero" then f = f.withMode mode: math.roundingmode.toZero
    if round == "awayFromZero" then f = f.withMode mode: math.roundingmode.awayFromZero
    if round == "toNegativeInf" then f = f.withMode mode: math.roundingmode.toNegativeInf
    if round == "toPositiveInf" then f = f.withMode mode: math.roundingmode.toPositiveInf
    same :t what: x got: (f.text :format :digits) :want
}

# decimalInit -- x * 2^shift spelled exactly in fixed format
decimalInit: function {t: Tally.borrow x: u64 shift: i64 digits: i64 want: StringView} is {
    f: (math.BigFloat.fromU64 from: x).scaleB n: shift
    same :t what: "decimal init" got: (f.text format: math.floatformat.fixed :digits) :want
}

# decimalRounding -- x rounded to nearest even at n digits
decimalRounding: function {t: Tally.borrow x: u64 n: i64 want: StringView} is {
    f: math.BigFloat.fromU64 from: x
    same :t what: "decimal rounding" got: (f.text format: math.floatformat.exponent digits: n - 1) :want
}

main: function is {
    t: Tally checks: 0 bad: 0
'''

TEXT_FOOTER = '''    if t.bad == 0 then print "ok \\{t.checks} checks" else print "\\{t.bad} of \\{t.checks} checks failed"
}
'''


def main():
    out = sys.argv[1]
    ft = go('float_test.go')
    rows = rows_float(ft)
    with open(os.path.join(out, 'math_bigfloat.z'), 'w') as f:
        f.write(FLOAT_HEADER + '\n'.join(rows) + '\n' + FLOAT_FOOTER)
    trows = rows_text(go('floatconv_test.go'), go('decimal_test.go'))
    with open(os.path.join(out, 'math_bigfloat_text.z'), 'w') as f:
        f.write(TEXT_HEADER + '\n'.join(trows) + '\n' + TEXT_FOOTER)
    print(f'{len(rows)} + {len(trows)} rows')


if __name__ == '__main__':
    main()
