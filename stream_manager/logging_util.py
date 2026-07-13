"""File logging for the request log (mirrors console output, auto-rotates)."""
import os

from .config import BASE_DIR

_log_file_path = None


def setup_file_logging(rel_path):
    global _log_file_path
    path = os.path.expandvars(rel_path)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    _log_file_path = path
    if os.path.isfile(path) and os.path.getsize(path) > 1048576:
        try:
            os.rename(path, path + ".old")
        except: pass


def write_file_log(plain):
    if _log_file_path:
        try:
            with open(_log_file_path, "a", encoding="utf-8") as _f:
                _f.write(plain + "\n")
        except: pass
