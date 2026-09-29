"""System-tray supervisor — owns the Stream Manager process.

The app can't restart itself: something outside it has to be alive when it
exits to start it again. That's this. It spawns stream-manager.py as a child,
waits, and relaunches whenever the child exits with lifecycle.EXIT_RESTART.

Restart and Quit from the menu go through the same HTTP endpoints the dashboard
buttons use, NOT through terminate(). Killing the child would skip the graceful
stop and drop cooldowns, spin history and session stats on the floor — which is
precisely the bug the shared teardown in cli.py exists to prevent.

    python -m stream_manager.tray

pystray and Pillow are optional, like psutil and websocket-client. Without them
this prints how to get them and exits; the launcher's own restart loop keeps
working either way.
"""
import os, subprocess, sys, threading, time, webbrowser

from . import __version__
from .config import BASE_DIR
from .ctl import _post, live_instance
from .lifecycle import EXIT_RESTART

ENTRY = os.path.join(BASE_DIR, "stream-manager.py")

# How long Quit waits for the child to save its state and let go of the port.
STOP_TIMEOUT = 15


class Supervisor:
    """Runs the child in a background thread and brings it back on code 42."""

    def __init__(self, args=()):
        self.args = list(args)
        self.proc = None
        self.stopping = False       # set by Quit: don't relaunch after this exit
        self._lock = threading.Lock()
        self._done = threading.Event()

    # ── process ──────────────────────────────────────────────────────────────
    def _spawn(self):
        env = dict(os.environ)
        env["SM_SUPERVISED"] = "1"      # this is the promise the Restart button checks
        return subprocess.Popen([sys.executable, ENTRY, *self.args],
                                cwd=BASE_DIR, env=env)

    def run_forever(self):
        try:
            while True:
                with self._lock:
                    if self.stopping:
                        break
                    self.proc = self._spawn()
                code = self.proc.wait()
                if self.stopping or code != EXIT_RESTART:
                    if not self.stopping:
                        print(f"[tray] Stream Manager exited with code {code} — not restarting.")
                    break
                print("[tray] Stream Manager asked to restart.")
        finally:
            self._done.set()

    def _current(self):
        """The child as of right now — never captured once and reused."""
        with self._lock:
            return self.proc

    # ── menu actions ─────────────────────────────────────────────────────────
    def _request(self, action):
        """Ask the running instance to stop the clean way. True if it accepted."""
        info, _ = live_instance()
        if not info:
            return False
        status, body = _post(info["port"], f"/api/lifecycle/{action}",
                             info["token"], {"confirm": True})
        if not (status == 200 and body.get("ok")):
            print(f"[tray] {action} refused: {body.get('error') or status}")
            return False
        return True

    def restart(self):
        if not self._request("restart"):
            print("[tray] Couldn't reach Stream Manager to restart it.")

    def open_dashboard(self):
        info, _ = live_instance()
        if info:
            webbrowser.open(f"http://localhost:{info['port']}/dashboard")

    def quit(self, timeout=STOP_TIMEOUT):
        """Stop the child for good. terminate() is the last resort, not step two.

        The graceful request is retried, not asked once, because live_instance()
        answers "no" for any child that is alive but not yet serving — and
        startup is several seconds of OBS and Twitch calls, longer on a
        first-run device login. Deciding from one failed probe meant
        TerminateProcess on a perfectly healthy child, which skips the save
        block entirely: cooldowns, spin history and session stats gone, and
        cooldowns.json possibly truncated on the way out. That is the exact
        data loss this feature exists to avoid.

        self.proc is re-read each pass rather than captured once: run_forever
        may have respawned between setting the flag and the first probe, and we
        would have sat waiting on a process that had already exited.
        """
        with self._lock:
            self.stopping = True          # from here, run_forever spawns nothing new

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            proc = self._current()
            if proc is None or proc.poll() is not None:
                break
            if time.monotonic() >= deadline:
                break
            self._request("shutdown")
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                continue

        proc = self._current()
        if proc is not None and proc.poll() is None:
            print("[tray] Stream Manager didn't stop in time — terminating.")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._done.wait(timeout=5)


def _icon_image(size=64):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m, b = size * 0.12, size * 0.86
    d.polygon([(size / 2, m), (b, b), (size - b, b)], fill=(140, 82, 255, 255))
    d.line([(size / 2, m), (size / 2, b)], fill=(64, 224, 208, 255), width=max(2, size // 20))
    return img


def run(args=()):
    try:
        import pystray
    except ImportError:
        print("\n  The tray icon needs two extra packages:\n\n"
              "      pip install pystray pillow\n\n"
              '  Until then, "Start Stream Manager.bat" restarts on its own '
              "and prism-ctl still works.\n")
        return 1
    try:
        image = _icon_image()
    except ImportError:
        print("\n  The tray icon needs Pillow:\n\n      pip install pillow\n")
        return 1

    if not os.path.isfile(ENTRY):
        print(f"  Can't find {ENTRY}")
        return 1

    sup = Supervisor(args)
    threading.Thread(target=sup.run_forever, name="sm-supervisor", daemon=True).start()

    icon = pystray.Icon("stream-manager", image, f"Stream Manager v{__version__}")

    def _on_quit(_icon, _item):
        sup.quit()
        _icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Open Dashboard", lambda *_: sup.open_dashboard(), default=True),
        pystray.MenuItem("Restart", lambda *_: sup.restart()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Stream Manager", _on_quit),
    )

    # If the child dies on its own (a crash, or Ctrl+C in its window), the tray
    # is the only thing left running — and an icon supervising nothing is worse
    # than no icon, because it looks like everything is fine.
    def _watch():
        sup._done.wait()
        time.sleep(0.5)
        icon.stop()
    threading.Thread(target=_watch, name="sm-tray-watch", daemon=True).start()

    icon.run()
    # Same budget as the menu item: this is the path a user takes by closing
    # the icon, and it was the one most likely to reach terminate().
    sup.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
