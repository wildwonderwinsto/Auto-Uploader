"""Controlled upload transport: never dispatches an HTTP request to Google."""

import time
from pathlib import Path

from account_support import ControlledOAuth

from shortspublisher.auth import UPLOAD_SCOPES
from shortspublisher.upload_http import INSERT_URL, UploadHTTP, UploadReply

REMOTE_ID = "TestVid123_"
SESSION = INSERT_URL + "?uploadType=resumable&upload_id=synthetic-capability"


class MemorySessions:
    def __init__(self):
        self.values = {}
        self.fail = False

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        if self.fail:
            raise RuntimeError("synthetic capability must never appear")
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class UploadOAuth(ControlledOAuth):
    def authorize_upload(self, config, cancel, progress):
        tokens = self.authorize(config, cancel, progress)
        tokens.scopes = UPLOAD_SCOPES
        return tokens

    def refresh(self, config, tokens, cancel):
        result = super().refresh(config, tokens, cancel)
        result.scopes = tokens.scopes
        return result


class ControlledUploadHTTP(UploadHTTP):
    def __init__(self, channel_id, remote_file: Path | None = None):
        self.channel_id = channel_id
        self.remote_file = remote_file
        self.received = bytearray(
            remote_file.read_bytes() if remote_file and remote_file.exists() else b""
        )
        self.size = 0
        self.initiations = 0
        self.offsets = []
        self.payload = None
        self.privacy = "private"
        self.processing = "processed"
        self.query_replies = []
        self.chunk_replies = []
        self.initiation_reply = None
        self.verify_reply = None
        self.verify_calls = 0
        self.lost_final = False
        self.after_chunk = None
        self.delay = 0

    def result(self):
        return {
            "id": REMOTE_ID,
            "snippet": {"channelId": self.channel_id},
            "status": {"privacyStatus": self.privacy, "uploadStatus": self.processing},
        }

    def request(self, method, url, tokens, *, headers=None, data=None, payload=None, params=None):
        headers = headers or {}
        if method == "POST":
            self.initiations += 1
            self.size = int(headers["X-Upload-Content-Length"])
            self.payload = payload
            return self.initiation_reply or UploadReply(200, headers={"location": SESSION})
        if method == "GET":
            self.verify_calls += 1
            return self.verify_reply or UploadReply(200, {"items": [self.result()]})
        assert url == SESSION
        content_range = headers["Content-Range"]
        self.size = int(content_range.split("/")[1])
        if content_range.startswith("bytes */"):
            if self.query_replies:
                return self.query_replies.pop(0)
            if len(self.received) == self.size:
                return UploadReply(201, self.result())
            return UploadReply(
                308, headers={"range": f"bytes=0-{len(self.received) - 1}"} if self.received else {}
            )
        offset = int(content_range.split(" ")[1].split("-")[0])
        self.offsets.append(offset)
        if self.chunk_replies:
            return self.chunk_replies.pop(0)
        assert offset == len(self.received)
        self.received.extend(data)
        if self.remote_file:
            self.remote_file.write_bytes(self.received)
        if self.after_chunk:
            self.after_chunk()
        if self.delay:
            time.sleep(self.delay)
        if len(self.received) == self.size:
            if self.lost_final:
                self.lost_final = False
                return UploadReply(0)
            return UploadReply(201, self.result())
        return UploadReply(308, headers={"range": f"bytes=0-{len(self.received) - 1}"})
