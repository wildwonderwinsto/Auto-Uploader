#!/bin/zsh
# One-time setup for ShortsScheduler on macOS: the Python environment, FFmpeg (to read video
# files) and ShortsScheduler.app. Run it again any time; it only updates what is needed.
#
#   ./install.sh                  # builds ShortsScheduler.app in this folder
#   ./install.sh --applications   # puts ShortsScheduler.app in ~/Applications instead
set -eu
cd -- "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

[[ "$(uname)" == Darwin ]] || { print "ShortsScheduler runs on macOS only."; exit 1 }

if ! command -v ffprobe >/dev/null; then
  if command -v brew >/dev/null; then
    print "Installing FFmpeg (ShortsScheduler uses it to read your video files)…"
    brew install ffmpeg
  else
    print "FFmpeg is needed to read video files. Install Homebrew from https://brew.sh,"
    print "then run:  brew install ffmpeg   and run ./install.sh again."
    exit 1
  fi
fi

if command -v uv >/dev/null; then
  print "Setting up the Python environment with uv…"
  uv sync --python 3.12
else
  python=$(command -v python3.13 || command -v python3.12 || command -v python3.11 || true)
  if [[ -z "$python" ]]; then
    print "Python 3.11 or newer is needed. Install uv (https://docs.astral.sh/uv/) or Python"
    print "from https://www.python.org, then run ./install.sh again."
    exit 1
  fi
  print "Setting up the Python environment with $python…"
  [[ -x .venv/bin/python ]] || "$python" -m venv .venv
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e .
fi

.venv/bin/python scripts/make_app.py "$@"
print ""
print "Done. Open ShortsScheduler.app to start (keep this project folder where it is)."
print "First time? The README's \"Set up your accounts\" section walks you through it."
