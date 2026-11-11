# generate tests/fixtures/emitc_corpus/overflow_methods.z and its golden, the
# expected values computed here, independently of the compiler
import sys
SIGNED = {'i8': 8, 'i16': 16, 'i32': 32, 'i64': 64, 'i128': 128}
UNSIGNED = {'u8': 8, 'u16': 16, 'u32': 32, 'u64': 64, 'u128': 128}
def lit(t, n, hi):
    if n >= 0: return f'{n}.{t}'
    if n == -hi - 1: return f'{hi}.{t}.not'
    return f'(0.{t} - {-n}.{t})'
src = ['''# The checked and saturating integer methods at every width, over the values
# where they differ from the wrapping operators: the ends of each type, the
# values next to them, 0, 1, -1 and a value near the square root of the range.
# The golden's values are computed outside the compiler (arbitrary precision).
''']
out = []
names = []
for table, sg in ((SIGNED, True), (UNSIGNED, False)):
    for t, n in table.items():
        lo, hi = (-(1 << (n - 1)), (1 << (n - 1)) - 1) if sg else (0, (1 << n) - 1)
        root = 1 << (n // 2)
        vals = [lo, lo + 1, -1, 0, 1, 2, root, -root, hi - 1, hi] if sg else [0, 1, 2, root - 1, root, hi - 1, hi]
        fn = 'check' + t.upper()
        names.append(fn)
        src.append(f'# {fn} -- every pair of {t}\'s edge values through the six methods\n{fn}: function is {{\n    vs: ListVal {t}\n')
        for v in vals: src.append(f'    vs.append {lit(t, v, hi)}\n')
        src.append('''    i: u64
    for i < vs.length loop {
        a: vs.get :i
        j: u64
        for j < vs.length loop {
            b: vs.get i: j
            ca: a.addChecked rhs: b
            cs: a.subChecked rhs: b
            cm: a.mulChecked rhs: b
            if ca.some then print "T add \\{a} \\{b} = \\{ca}" else print "T add \\{a} \\{b} = none"
            if cs.some then print "T sub \\{a} \\{b} = \\{cs}" else print "T sub \\{a} \\{b} = none"
            if cm.some then print "T mul \\{a} \\{b} = \\{cm}" else print "T mul \\{a} \\{b} = none"
            print "T sat \\{a} \\{b} = \\{(a.addSaturating rhs: b)} \\{(a.subSaturating rhs: b)} \\{(a.mulSaturating rhs: b)}"
            j = j + 1
        }
        i = i + 1
    }
}

'''.replace('T ', t + ' '))
        clamp = lambda x: max(lo, min(hi, x))
        ck = lambda x: str(x) if lo <= x <= hi else 'none'
        for a in vals:
            for b in vals:
                out.append(f'{t} add {a} {b} = {ck(a + b)}')
                out.append(f'{t} sub {a} {b} = {ck(a - b)}')
                out.append(f'{t} mul {a} {b} = {ck(a * b)}')
                out.append(f'{t} sat {a} {b} = {clamp(a + b)} {clamp(a - b)} {clamp(a * b)}')
src.append('main: function is {\n' + ''.join(f'    {n}\n' for n in names) + '}\n')
open('tests/fixtures/emitc_corpus/overflow_methods.z', 'w').write(''.join(src))
open('tests/fixtures/run_golden/overflow_methods.out', 'w').write('\n'.join(out) + '\n')
print(len(out), 'golden lines')
