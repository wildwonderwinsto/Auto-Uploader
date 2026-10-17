#!/bin/zsh
# Double-click in Finder to open the established ShortsPublisher workspace.
set -eu
cd -- "${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

fail() {
  print -r -- "$1"
  read -r '?Press Return to close this window.'
  exit 1
}

[[ -x .venv/bin/python ]] || fail 'Python environment missing. Follow the installation instructions in README.md.'
[[ -f artifacts/stage6-live/library.sqlite3 ]] || fail 'The existing library is missing from artifacts/stage6-live. Restore it before launching.'

# SQLite's backup API produces a consistent snapshot, including committed WAL data.
.venv/bin/python - <<'PY' || fail 'Library backup failed. The application was not started.'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path('artifacts/stage6-live/library.sqlite3').resolve()
backups = source.parent / 'backups'
backups.mkdir(exist_ok=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
destination = backups / f'library-before-launch-{stamp}.sqlite3'
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
    with sqlite3.connect(destination) as backup:
        original.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Backup integrity check failed')
print(f'Library backup saved: {destination}')
PY

print 'Starting ShortsPublisher. Close the application normally before closing this Terminal window.'
.venv/bin/python -m shortspublisher --data-dir "$PWD/artifacts/stage6-live" || fail 'ShortsPublisher exited with an error. See the messages above.'
