#!/usr/bin/env python3
"""
Stream Manager — web dashboard + overlay server + system monitor
"""
from stream_manager.cli import main

if __name__ == "__main__":
    # The exit code is the restart signal: the launcher and the tray both
    # relaunch on stream_manager.lifecycle.EXIT_RESTART (42) and stop on
    # anything else. Swallowing it here would break both.
    raise SystemExit(main())
