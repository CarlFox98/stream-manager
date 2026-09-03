"""File logging for the request log (mirrors console output, auto-rotates).

Every writer in the app funnels through `append_line`, which holds a lock for the
whole open-write-close cycle and rotates on size as it goes.

Both of those exist because of real damage. The request log is written from every
handler thread of a ThreadingHTTPServer, and the previous version opened the file
fresh on each call with no lock at all. A stream on 2026-09-02 left eight torn
fragments in server.log -- lines consisting of a single `l` and a stray carriage
return -- where two threads' writes collided mid-line. Rotation was also checked
only once at startup, so leaving the app running across several streams grew the
file without bound (~75 KB per two-hour stream, so the 1 MB ceiling was about a
day of streaming away).
"""
import os, threading

from .config import BASE_DIR

MAX_BYTES = 1048576          # 1 MB, then roll to <name>.old

_log_file_path = None
_lock = threading.Lock()     # guards every append + rotation in this process


def _resolve(rel_path):
    path = os.path.expandvars(rel_path)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return path


def _rotate_if_needed(path):
    """Roll `path` to `path.old` once it passes MAX_BYTES. Caller holds _lock."""
    try:
        if os.path.isfile(path) and os.path.getsize(path) > MAX_BYTES:
            old = path + ".old"
            if os.path.isfile(old):
                os.remove(old)      # os.rename won't overwrite on Windows
            os.rename(path, old)
    except Exception:
        pass                        # logging must never take the app down


def append_line(path, line):
    """Append one line to `path`, atomically with respect to other threads.

    newline="" stops Python's text layer translating \\n into \\r\\n as a separate
    write, which is what turned a collided line into a bare carriage return.
    """
    try:
        with _lock:
            _rotate_if_needed(path)
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8", newline="") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


def setup_file_logging(rel_path):
    global _log_file_path
    path = _resolve(rel_path)
    _log_file_path = path
    with _lock:
        _rotate_if_needed(path)


def write_file_log(plain):
    if _log_file_path:
        append_line(_log_file_path, plain)
