"""CLI argument parsing and the main entry point / startup banner."""
import argparse, os, sys, threading, webbrowser

from . import __version__
from . import obs, system, twitch, updater
from .config import config, TWITCH_USER
from .console import style, icon
from .logging_util import setup_file_logging
from .scenes import ACTIVE_DIR, ACTIVE_DIRNAME, available_sets, detect_active_set
from .server import try_bind_port
from .state import state


def parse_args():
    p = argparse.ArgumentParser(description="Stream Manager — web dashboard + overlay server + system monitor")
    p.add_argument("--port", type=int, default=0, help="Port to listen on (overrides config.json)")
    p.add_argument("--poll", type=int, default=0, help="Poll interval in seconds (overrides config.json)")
    p.add_argument("--no-browser", action="store_true", help="Don't open dashboard in browser")
    p.add_argument("--lan", action="store_true", help="Bind to 0.0.0.0 so other devices on your network can reach the dashboard (overrides config.json)")
    p.add_argument("--check-update", action="store_true", help="Check GitHub for a newer version and exit")
    p.add_argument("--update", action="store_true", help="Check, then (after confirmation) download & install the latest version")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()


def main():
    args = parse_args()

    # ── Update flags (handled before starting the server) ──
    if args.check_update or args.update:
        print(f"\n  {style('C', 'Checking for updates…')}  (current v{__version__})")
        updater.check_for_update()
        if updater.update_state["error"]:
            print(f"  {style('R', '✗')} Update check failed: {updater.update_state['error']}\n")
            sys.exit(1)
        if not updater.update_state["available"]:
            print(f"  {style('G', '✓')} You're on the latest version (v{__version__}).\n")
            sys.exit(0)
        latest = updater.update_state["latest"]
        print(f"  {style('Y', '●')} New version available: {style('B', latest)}  (you have v{__version__})")
        if updater.update_state["notes"]:
            print(f"  {style('D', 'Release notes:')}\n{updater.update_state['notes']}")
        if args.check_update and not args.update:
            print(f"\n  Run {style('W', 'python stream-manager.py --update')} to install it.\n")
            sys.exit(0)
        # --update: confirm, then download + install
        try:
            ans = input(f"\n  Download and install {latest}? Your current files will be backed up. [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans not in ("y", "yes"):
            print("  Update cancelled.\n"); sys.exit(0)
        ok, msg, staged = updater.download_update()
        if not ok:
            print(f"  {style('R', '✗')} {msg}\n"); sys.exit(1)
        ok, msg = updater.install_update(staged)
        mark = style('G', '✓') if ok else style('R', '✗')
        print(f"  {mark} {msg}\n")
        sys.exit(0 if ok else 1)

    if args.port:
        config["port"] = args.port
    if args.poll:
        config["poll_interval"] = args.poll
    if args.lan:
        config["lan"] = True
    setup_file_logging(config["log_file"])
    bind_host = "0.0.0.0" if config["lan"] else "127.0.0.1"
    server, PORT = try_bind_port(config["port"], bind_host)
    state["server"]["port"] = PORT
    server.timeout = 0.5

    M = style("M", "┃")
    B = style("D", "─")
    def heading(text): return f"  {M}  {style('W', style('B', text))}"
    def info(k, v):   return f"  {M}    {style('C', k)}  {v}"

    box_top    = f"  {style('M', '┏')}{B*58}{style('M', '┓')}"
    box_bot    = f"  {style('M', '┗')}{B*58}{style('M', '┛')}"
    box_div    = f"  {style('M', '┣')}{B*58}{style('M', '┫')}"
    separator  = f"  {style('D', '─')*60}"
    blank_row  = f"  {M}  {'':55s}{M}"

    # Pre-check services
    obs.get_obs_status(state)
    system.get_system_stats(state)
    system.get_gpu_stats(state)
    token = twitch.get_access_token()
    tw_ok = token is not None
    if tw_ok:
        twitch.get_twitch_status(state)
        twitch.get_twitch_user_info(state)

    dn = state["twitch"]["display_name"] or TWITCH_USER
    vc = state["twitch"]["view_count"]
    live_str = f"{icon(state['twitch']['live'])} {style('B' if state['twitch']['live'] else 'D', 'LIVE' if state['twitch']['live'] else 'Offline')}"

    nav = [
        ("Dashboard", f"http://localhost:{PORT}/dashboard"),
        ("API",       f"http://localhost:{PORT}/api/status"),
        ("Scenes",    f"http://localhost:{PORT}/api/scenes"),
        ("Update",    f"http://localhost:{PORT}/api/update"),
        ("Health",    f"http://localhost:{PORT}/api/health"),
    ]

    # Scene-set detection for the banner
    scene_avail = available_sets()
    scene_active = detect_active_set()
    state["scenes"]["available"] = scene_avail
    state["scenes"]["active_set"] = scene_active

    overlays = []
    if os.path.isdir(ACTIVE_DIR):
        overlays = sorted(f for f in os.listdir(ACTIVE_DIR) if f.lower().endswith(".html"))

    print()
    print(box_top)
    ver = style('D', f'v{__version__}')
    print(f"  {M}  {style('M', style('B', '▄▄  Stream Manager'))}  {ver}     {style('D', 'Web dashboard + overlay server + system monitor')}  {M}")
    print(box_div)
    print(heading("Server"))
    print(info("●", f"http://localhost:{PORT}"))
    if config["lan"]:
        print(info("!", style('Y', "Bound to 0.0.0.0 — reachable by anyone on your network (--lan)")))
    else:
        print(info("i", style('D', "Bound to 127.0.0.1 — local only. Use --lan to expose on your network")))
    for name, url in nav:
        print(info(f" {style('D', '→')}", f"{style('W', name):12s}  {style('D', url)}"))
    print(blank_row)
    print(heading("Channels"))
    print(info("Twitch", f"{style('W', dn):22s} {icon(tw_ok)} {style('G' if tw_ok else 'R', 'Connected' if tw_ok else 'No credentials')}  {live_str}  {style('D', f'{vc:,} views' if vc else '')}"))
    obs_icon = icon(state['obs']['running'])
    obs_str = f"Running (PID {state['obs']['pid']})" if state['obs']['running'] else "Not running"
    print(info("OBS   ", f"{obs_icon} {style('D', obs_str)}"))
    sys_str = f"{state['system']['cpu']}% CPU  ·  {state['system']['ram_used_gb']}/{state['system']['ram_total_gb']} GB RAM"
    gpu_name = state['system']['gpu'] or "—"
    print(info("System", f"{icon(True)} {style('D', sys_str)}  ·  {gpu_name}"))
    print(blank_row)
    print(heading("Scene Set"))
    _set_labels = {"modern": "Modern Neon", "retro": "Retro Win98"}
    if scene_active:
        _lbl = _set_labels.get(scene_active, scene_active)
        print(info("Active", f"{icon(True)} {style('B', style('C', _lbl))}"))
    else:
        print(info("Active", f"{icon(False)} {style('Y', 'Unknown / not set')}"))
    if scene_avail:
        _av = ", ".join(_set_labels.get(n, n) + (style('G', ' ✓') if n == scene_active else "") for n in scene_avail)
        print(info("Sets  ", style('D', _av)))
    else:
        print(info("Sets  ", style('Y', "none found — create overlays/modern/ and overlays/retro/")))
    print(blank_row)
    print(heading("Overlays"))
    if overlays:
        for ov in overlays:
            print(info(f" {style('D', '▸')}", style('D', f"/overlays/{ACTIVE_DIRNAME}/{ov}")))
    else:
        print(info("", style('Y', "No overlays in active/ — switch a scene set from the dashboard")))
    print(blank_row)
    print(heading("Polling"))
    poll_str = f"every {config['poll_interval']}s"
    print(info(f" {style('D', '↻')}", style('D', poll_str)))
    print(box_bot)
    print()
    print(separator)
    flags = " --port / --poll / --no-browser / --lan / --check-update / --update"
    print(f"  {style('D', f'Ctrl+C to stop · config.json · flags:{flags}')}")
    print(separator)
    print()

    # Non-blocking startup update check — a slow/failed GitHub call never delays startup
    def _bg_update_check():
        updater.check_for_update()
        if updater.update_state.get("available"):
            msg = f"Update available: {updater.update_state['latest']} (you have v{__version__}) — run --update or use the dashboard"
            print(f"  {style('Y', '●')} {style('Y', msg)}\n")
    threading.Thread(target=_bg_update_check, daemon=True).start()

    if not args.no_browser:
        webbrowser.open(f"http://localhost:{PORT}/dashboard")

    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print(f"\n  {style('Y', 'Shutdown.')}")
        server.server_close()
