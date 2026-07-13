"""
Update checking / self-update.

Checks GitHub Releases for a newer tagged version and can download + install
it, but NEVER installs without explicit confirmation. Since the app is now a
multi-file source tree (stream-manager.py + the stream_manager/ package +
static/), updates are fetched as GitHub's auto-generated release source
archive (the "zipball") rather than a single raw file, and only the app's own
paths are replaced — config.json, .env, logs, and backups are left alone.
"""
import ast, io, json, os, shutil, tempfile, urllib.error, urllib.request, zipfile

from . import __version__
from .config import BASE_DIR

GITHUB_OWNER = "CarlFox98"
GITHUB_REPO = "stream-manager"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Paths (relative to BASE_DIR) that an update is allowed to replace.
SYNCED_PATHS = ["stream-manager.py", "stream_manager", "static", "requirements.txt"]
BACKUP_DIR = os.path.join(BASE_DIR, ".update-backup")

update_state = {"checked": False, "latest": None, "current": __version__,
                "available": False, "error": None, "notes": "", "download_url": None}


def _parse_version(v):
    """'v1.2.3' or '1.2.3' -> (1,2,3). Non-numeric parts sort as 0."""
    v = (v or "").lstrip("vV").strip()
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit(): num += ch
            else: break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _http_get(url, accept=None, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"stream-manager/{__version__}",
        "Accept": accept or "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_for_update():
    """Query GitHub Releases. Populates update_state. Read-only, never writes."""
    update_state["checked"] = True
    update_state["error"] = None
    try:
        data = json.loads(_http_get(GITHUB_API_LATEST, accept="application/vnd.github+json"))
        tag = data.get("tag_name") or ""
        update_state["latest"] = tag
        update_state["notes"] = (data.get("body") or "").strip()[:500]
        # GitHub auto-generates a source zipball for every release/tag
        update_state["download_url"] = data.get("zipball_url") or (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/zipball/{tag}" if tag else None
        )
        update_state["available"] = bool(tag) and _parse_version(tag) > _parse_version(__version__)
        return update_state
    except urllib.error.HTTPError as e:
        update_state["error"] = f"GitHub returned HTTP {e.code}" + (" (no releases yet?)" if e.code == 404 else "")
    except Exception as e:
        update_state["error"] = str(e)
    update_state["available"] = False
    return update_state


def _validate_staged_tree(root):
    """Reject anything that isn't a plausible, parseable stream-manager source tree."""
    entry = os.path.join(root, "stream-manager.py")
    if not os.path.isfile(entry):
        return False, "Downloaded release is missing stream-manager.py"
    try:
        with open(entry, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return False, f"Could not read staged stream-manager.py: {e}"
    if len(text) < 50 or "Stream Manager" not in text:
        return False, "Downloaded stream-manager.py doesn't look right"
    try:
        ast.parse(text)
    except SyntaxError as e:
        return False, f"Downloaded stream-manager.py has a syntax error: {e}"

    pkg_dir = os.path.join(root, "stream_manager")
    if not os.path.isdir(pkg_dir):
        return False, "Downloaded release is missing the stream_manager package"
    for dirpath, _dirs, files in os.walk(pkg_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    ast.parse(f.read())
            except (OSError, SyntaxError) as e:
                return False, f"{os.path.relpath(path, root)} is invalid: {e}"
    return True, "ok"


def download_update():
    """
    Download the release source archive to a staging directory. Returns
    (ok, message, staged_root|None). Does NOT install anything.
    """
    url = update_state.get("download_url")
    if not url:
        return False, "No download URL — run a version check first", None
    try:
        raw = _http_get(url, accept="application/vnd.github+json", timeout=30)
    except Exception as e:
        return False, f"Download failed: {e}", None

    stage_dir = tempfile.mkdtemp(prefix="stream-manager-update-")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            zf.extractall(stage_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return False, "Downloaded file isn't a valid archive", None

    # GitHub zipballs contain exactly one top-level folder: owner-repo-sha/
    entries = [e for e in os.listdir(stage_dir) if os.path.isdir(os.path.join(stage_dir, e))]
    if len(entries) != 1:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return False, "Unexpected archive layout", None
    root = os.path.join(stage_dir, entries[0])

    ok, why = _validate_staged_tree(root)
    if not ok:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return False, why, None
    return True, f"Downloaded {update_state.get('latest')}", root


def install_update(staged_root):
    """
    Back up the current app files and swap in the staged ones. Caller is
    responsible for having obtained confirmation first. Returns (ok, message).
    Only SYNCED_PATHS are touched — config.json, .env, logs, and old backups
    are left alone. Backs everything up before changing anything, so a
    failure during backup leaves the current install untouched.
    """
    if not staged_root or not os.path.isdir(staged_root):
        return False, "No staged update found — download first"
    ok, why = _validate_staged_tree(staged_root)
    if not ok:
        return False, f"Refusing to install: {why}"

    try:
        if os.path.isdir(BACKUP_DIR):
            shutil.rmtree(BACKUP_DIR)
        os.makedirs(BACKUP_DIR)

        # Phase 1: back up everything that currently exists.
        for rel in SYNCED_PATHS:
            cur = os.path.join(BASE_DIR, rel)
            if not os.path.exists(cur):
                continue
            backup_target = os.path.join(BACKUP_DIR, rel)
            if os.path.isdir(cur):
                shutil.copytree(cur, backup_target)
            else:
                shutil.copy2(cur, backup_target)

        # Phase 2: swap in the staged copies.
        for rel in SYNCED_PATHS:
            new = os.path.join(staged_root, rel)
            if not os.path.exists(new):
                continue  # release doesn't ship this optional path; leave current alone
            cur = os.path.join(BASE_DIR, rel)
            if os.path.isdir(cur):
                shutil.rmtree(cur)
            elif os.path.isfile(cur):
                os.remove(cur)
            if os.path.isdir(new):
                shutil.copytree(new, cur)
            else:
                shutil.copy2(new, cur)

        shutil.rmtree(os.path.dirname(staged_root), ignore_errors=True)  # clean staging temp dir
        return True, "Installed update. Previous version backed up to .update-backup/. Restart to apply."
    except Exception as e:
        return False, f"Install failed ({e}) — check .update-backup/ if files look mixed up"
