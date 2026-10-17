"""Read-only cancellable source integrity helpers shared by manual uploads and planning."""

import hashlib
import os
from threading import Event
from typing import BinaryIO

from .auth import AccountError, check_cancel


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)


def fingerprint(file: BinaryIO, cancel: Event) -> str:
    before = signature(os.fstat(file.fileno()))
    digest = hashlib.sha256()
    file.seek(0)
    while block := file.read(1024 * 1024):
        check_cancel(cancel)
        digest.update(block)
    if signature(os.fstat(file.fileno())) != before:
        raise AccountError(
            "Source changed while fingerprinting. Refresh inspection and review again.", "source"
        )
    file.seek(0)
    return digest.hexdigest()
