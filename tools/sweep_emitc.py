"""Sweep every example through both emitters: one summary line each
(CLEAN, or the first failing stage). The emitter-port admission scout.

Usage: uv run python tools/sweep_emitc.py   (repo root, /tmp/zc built)
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, "tools")
from probe_emitc import build, emit_ref, emit_z, run_binary

names = sorted(
    os.path.splitext(os.path.basename(p))[0] for p in glob.glob("examples/*.z")
)
clean = 0
for unit in names:
    tmp = tempfile.mkdtemp(prefix=f"emitc_{unit}_")
    try:
        verdict = "CLEAN"
        ref_c = os.path.join(tmp, "ref.c")
        z_c = os.path.join(tmp, "z.c")
        try:
            if emit_ref(unit, ref_c).returncode != 0:
                verdict = "REF-FAIL"
            elif emit_z("/tmp/zc", unit, z_c).returncode != 0 or not os.path.exists(
                z_c
            ):
                verdict = "ZC-FAIL"
            elif build(ref_c, os.path.join(tmp, "ref.bin")).returncode != 0:
                verdict = "REF-CC-FAIL"
            elif build(z_c, os.path.join(tmp, "z.bin")).returncode != 0:
                verdict = "Z-CC-FAIL"
            else:
                rd = os.path.join(tmp, "ref_run")
                zd = os.path.join(tmp, "z_run")
                os.mkdir(rd)
                os.mkdir(zd)
                ref_res = run_binary(os.path.join(tmp, "ref.bin"), rd)
                z_res = run_binary(os.path.join(tmp, "z.bin"), zd)
                if ref_res[:2] != z_res[:2]:
                    verdict = "RUN-DIFF"
        except subprocess.TimeoutExpired:
            verdict = "TIMEOUT"
        except SystemExit:
            verdict = "ZC-TIMEOUT"
        if verdict == "CLEAN":
            clean += 1
        print(f"{unit:24s} {verdict}", flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
print(f"-- {clean} of {len(names)} CLEAN")
