"""Controlled account providers. Never contact Google or real user credential storage."""

import time
from threading import Event

from shortspublisher.auth import AccountError, ClientConfig, GoogleOAuth, Tokens, check_cancel
from shortspublisher.youtube import Channel, YouTubeClient

CONFIG = ClientConfig("123456789-test.apps.googleusercontent.com", "public-desktop-value")
CHANNEL = Channel(
    "UC_test_channel",
    "Controlled test channel — 雪",
    "@controlled-test",
    "Test service only; not live Google authorization.",
)


class MemoryCredentials:
    def __init__(self):
        self.values = {}
        self.fail = ""

    def get(self, key):
        if self.fail == "get":
            raise AccountError("Test Keychain read denied", "storage")
        return self.values.get(key)

    def put(self, key, tokens):
        if self.fail == "put":
            raise AccountError("Test Keychain write denied", "storage")
        self.values[key] = tokens

    def delete(self, key):
        if self.fail == "delete":
            raise AccountError("Test Keychain removal denied", "storage")
        self.values.pop(key, None)


class ControlledOAuth(GoogleOAuth):
    def __init__(self):
        self.wait = False
        self.entered = Event()
        self.refresh_count = 0
        self.revoke_ok = True
        self.error = None

    def authorize(self, config, cancel, progress):
        self.entered.set()
        progress("Controlled test: waiting for authorization; no browser or Google request.")
        while self.wait and not cancel.wait(0.02):
            pass
        check_cancel(cancel)
        if self.error:
            raise self.error
        return Tokens("test-access-sensitive", "test-refresh-sensitive", time.time() + 3600)

    def refresh(self, config, tokens, cancel):
        self.refresh_count += 1
        if self.error:
            raise self.error
        return Tokens("test-access-rotated", "test-refresh-rotated", time.time() + 3600)

    def revoke(self, tokens):
        return self.revoke_ok


class ControlledYouTube(YouTubeClient):
    def __init__(self):
        self.items = [CHANNEL]
        self.error = None
        self.calls = 0

    def channels(self, tokens, cancel):
        self.calls += 1
        check_cancel(cancel)
        if self.error:
            raise self.error
        return self.items

    def statistics(self, tokens, channel_id, video_ids, cancel):
        self.statistics_tokens = tokens
        return {"channel": {"views": 5}, "videos": {v: {"views": 1} for v in video_ids}}

    def thumbnail(self, channel, cancel):
        return b""
