"""Probe one example through both emitters: the reference (zc.py) C + binary
vs the ported (/tmp/zc --emit-c) C + binary. The gate is build parity under
the golden cc flags plus identical stdout and exit code (type ids differ by
construction, so the C is never byte-compared).

Usage: uv run python tools/probe_emitc.py <unit> [--zc /tmp/zc] [--keep]
Run from the repo root with a freshly built /tmp/zc.
"""

import argparse
import os
import resource
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = os.getcwd()
SYSTEM_DIR = os.path.join(REPO_ROOT, "lib", "system")
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")

CFLAGS = [
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Wno-unused-function",
    "-Wno-unused-parameter",
    "-Werror=implicit-function-declaration",
    "-Werror=implicit-int",
    "-Werror=int-conversion",
    "-Werror=incompatible-pointer-types",
]

# Runaway guards: a non-terminating child must die at the cap instead of
# swapping the machine to death. RLIMIT_AS is safe here (no ASan).
ZC_TIMEOUT_S = 60
ZC_MEM_LIMIT = 1536 * 1024 * 1024
RUN_TIMEOUT_S = 30
RUN_MEM_LIMIT = 512 * 1024 * 1024


def _cap(limit):
    def pre():
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return pre


def _run(args, timeout, mem, **kw):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        preexec_fn=_cap(mem),
        **kw,
    )


def emit_ref(unit, out_c):
    proc = _run(
        [sys.executable, "src/zc.py", unit, "--src", EXAMPLES_DIR, "-o", out_c],
        240,
        ZC_MEM_LIMIT,
    )
    return proc


def emit_z(zc, unit, out_c):
    args = [zc, unit, "--src", EXAMPLES_DIR, "--system", SYSTEM_DIR, "--emit-c", out_c]
    try:
        return _run(args, ZC_TIMEOUT_S, ZC_MEM_LIMIT)
    except subprocess.TimeoutExpired:
        print(f"zc timed out (>{ZC_TIMEOUT_S}s) on {unit}")
        sys.exit(2)


def build(c_path, bin_path):
    return _run(["gcc", *CFLAGS, "-o", bin_path, c_path], 120, ZC_MEM_LIMIT)


def run_binary(bin_path, workdir):
    try:
        proc = _run([bin_path], RUN_TIMEOUT_S, RUN_MEM_LIMIT, cwd=workdir)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return None, "", f"timed out (>{RUN_TIMEOUT_S}s)"


def probe(unit, zc, keep):
    tmp = tempfile.mkdtemp(prefix=f"emitc_{unit}_")
    ref_c = os.path.join(tmp, "ref.c")
    z_c = os.path.join(tmp, "z.c")
    ok = True
    try:
        rp = emit_ref(unit, ref_c)
        if rp.returncode != 0:
            print(f"== REF-FAIL: zc.py exited {rp.returncode}\n{rp.stderr[:800]}")
            return False
        print(f"== ref emit: OK ({sum(1 for _ in open(ref_c))} lines)")

        zp = emit_z(zc, unit, z_c)
        if zp.returncode != 0 or not os.path.exists(z_c):
            print(f"== ZC-FAIL: zc exited {zp.returncode}\nstderr:\n{zp.stderr[:800]}")
            return False
        print(f"== z emit:   OK ({sum(1 for _ in open(z_c))} lines)")

        rb = build(ref_c, os.path.join(tmp, "ref.bin"))
        if rb.returncode != 0:
            print(f"== REF-CC-FAIL:\n{rb.stderr[:800]}")
            return False
        zb = build(z_c, os.path.join(tmp, "z.bin"))
        if zb.returncode != 0:
            print(f"== Z-CC-FAIL:\n{zb.stderr[:1500]}")
            return False
        print("== both compile (golden flags)")

        ref_dir = os.path.join(tmp, "ref_run")
        z_dir = os.path.join(tmp, "z_run")
        os.mkdir(ref_dir)
        os.mkdir(z_dir)
        rrc, rout, rerr = run_binary(os.path.join(tmp, "ref.bin"), ref_dir)
        zrc, zout, zerr = run_binary(os.path.join(tmp, "z.bin"), z_dir)
        if (rrc, rout) == (zrc, zout):
            print(f"== RUN: MATCH (exit {rrc}, {len(rout)} bytes of stdout)")
        else:
            ok = False
            print(f"== RUN-DIFF: ref exit={rrc} z exit={zrc}")
            rl, zl = rout.splitlines(), zout.splitlines()
            for i in range(max(len(rl), len(zl))):
                a = rl[i] if i < len(rl) else "<absent>"
                b = zl[i] if i < len(zl) else "<absent>"
                if a != b:
                    print(f"   line {i + 1}: ref {a!r}  z {b!r}")
            if zerr:
                print(f"   z stderr: {zerr[:300]}")
        return ok
    finally:
        if keep:
            print(f"== artifacts kept in {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("unit")
    ap.add_argument("--zc", default="/tmp/zc")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    sys.exit(0 if probe(args.unit, args.zc, args.keep) else 1)


if __name__ == "__main__":
    main()
