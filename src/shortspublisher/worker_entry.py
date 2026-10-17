"""Absolute-path launchd entry point for the installed development environment."""

from shortspublisher.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
