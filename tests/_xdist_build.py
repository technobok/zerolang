"""Build a heavy compiler artifact once and share it across pytest-xdist workers.

Without ``-n`` parallelism this is a no-op (builds normally). Under xdist each
worker is a separate session that would otherwise rebuild every heavy binary --
``zc`` alone is ~5 MB of C / ~3 min via the reference compiler, and there are
~15 such binaries (zc, the lexer/parser/vfs/ast/types/typing/env units in two
variants each, stage2, stage2-asan). N workers each rebuilding all of them, two
at a time, exhausts a small box; a single OOM-killed build fails a *session*
fixture and cascades to every test that needs it.

This builds each artifact once, behind a single global file lock so heavy builds
never run concurrently, and publishes it atomically to a worker-shared path that
the other workers reuse. Tests still run in parallel -- only the builds serialize.
"""

import fcntl
import os
import shutil


def build_once_shared(key, tmp_path_factory, build_fn):
    """Return the path to a binary produced by ``build_fn(dest_dir) -> path``.

    ``key`` names the artifact. Built once and shared across xdist workers; when
    not running under xdist, builds directly into a fresh tmp dir.
    """
    if os.environ.get("PYTEST_XDIST_WORKER") is None:
        return build_fn(tmp_path_factory.mktemp(key))
    shared = tmp_path_factory.getbasetemp().parent
    target = shared / f"shared-{key}"
    if target.exists():  # fast path -- already built by another worker
        return str(target)
    with open(str(shared / "shared-build.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not target.exists():  # re-check under the lock
            built = build_fn(tmp_path_factory.mktemp(key))
            partial = f"{target}.{os.getpid()}.partial"
            shutil.copyfile(built, partial)
            os.chmod(partial, 0o755)
            os.replace(partial, str(target))  # atomic publish
    return str(target)
