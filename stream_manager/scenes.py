"""
Scene set management.

Layout on disk:
  overlays/modern/   full set of scene .html files (modern neon theme)
  overlays/retro/    full set of scene .html files (retro Win98 theme)
  overlays/active/   a copy of whichever set is currently live
OBS points at /overlays/active/<scene>.html and never changes; switching
a set means replacing the contents of active/ with the chosen set.
"""
import hashlib, os, shutil

from .config import OVERLAYS_DIR

SCENE_SETS = ["modern", "retro"]
ACTIVE_DIRNAME = "active"
ACTIVE_DIR = os.path.join(OVERLAYS_DIR, ACTIVE_DIRNAME)
# Manifest records which set was last applied to active/ (source of truth).
ACTIVE_MANIFEST = os.path.join(ACTIVE_DIR, ".active-set")


def set_dir(name):
    return os.path.join(OVERLAYS_DIR, name)


def available_sets():
    """Return the subset of SCENE_SETS that actually exist on disk with files."""
    out = []
    for name in SCENE_SETS:
        d = set_dir(name)
        if os.path.isdir(d) and any(f.lower().endswith(".html") for f in os.listdir(d)):
            out.append(name)
    return out


def _dir_signature(d):
    """Stable hash of a directory's *.html filenames + contents, for fallback detection."""
    if not os.path.isdir(d):
        return None
    h = hashlib.sha256()
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".html"):
            continue
        h.update(fn.encode("utf-8"))
        try:
            with open(os.path.join(d, fn), "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()


def detect_active_set():
    """
    Return the name of the set currently in active/, or None if unknown/empty.
    Prefers the manifest; falls back to content-matching against known sets.
    """
    if not os.path.isdir(ACTIVE_DIR):
        return None
    # 1. Trust the manifest if present and valid
    try:
        with open(ACTIVE_MANIFEST, encoding="utf-8") as f:
            name = f.read().strip()
        if name in SCENE_SETS:
            return name
    except OSError:
        pass
    # 2. Fallback: match active/ contents against each known set
    active_sig = _dir_signature(ACTIVE_DIR)
    if active_sig:
        for name in SCENE_SETS:
            if _dir_signature(set_dir(name)) == active_sig:
                return name
    return None


def apply_scene_set(name):
    """
    Copy overlays/<name>/ into overlays/active/, replacing it, and write the
    manifest. Returns (ok, message). Never raises to the caller.
    """
    if name not in SCENE_SETS:
        return False, f"Unknown scene set '{name}'"
    src = set_dir(name)
    if not os.path.isdir(src):
        return False, f"Scene set '{name}' not found on disk"
    try:
        os.makedirs(ACTIVE_DIR, exist_ok=True)
        # Clear existing active/ contents (files + subdirs), keep the dir itself
        for entry in os.listdir(ACTIVE_DIR):
            p = os.path.join(ACTIVE_DIR, entry)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try: os.remove(p)
                except OSError: pass
        # Copy the chosen set in
        for entry in os.listdir(src):
            s = os.path.join(src, entry)
            d = os.path.join(ACTIVE_DIR, entry)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        with open(ACTIVE_MANIFEST, "w", encoding="utf-8") as f:
            f.write(name)
        return True, f"Switched active scene set to '{name}'"
    except Exception as e:
        return False, f"Failed to switch scene set: {e}"
