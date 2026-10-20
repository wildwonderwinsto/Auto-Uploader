"""Controlled Buffer GraphQL/OAuth and Cloudinary endpoints. No network, no real credentials."""

import base64
import hashlib
from typing import Any

import requests

from shortspublisher.buffer import API_URL, TOKEN_URL


class Response:
    def __init__(self, status: int, body: Any = None, headers: dict | None = None):
        self.status_code, self.body, self.headers = status, body, headers or {}

    def json(self) -> Any:
        if self.body is None:
            raise ValueError("no json")
        return self.body


class Secrets:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


CHANNELS = [
    {
        "id": "ch-tt",
        "name": "mytiktok",
        "displayName": "My TikTok",
        "service": "tiktok",
        "avatar": "",
        "isDisconnected": False,
        "isLocked": False,
    },
    {
        "id": "ch-ig",
        "name": "insta",
        "displayName": "Insta",
        "service": "instagram",
        "avatar": "",
        "isDisconnected": False,
        "isLocked": False,
    },
]


class FakeBuffer:
    """Rotating refresh tokens (reuse revokes the grant), posts with Buffer's documented states."""

    def __init__(self):
        self.challenge = ""
        self.serial = 0
        self.access: set[str] = set()
        self.refresh_valid: set[str] = set()
        self.revoked = False
        self.refreshes = 0
        self.refresh_fail: str | None = None
        self.posts: dict[str, dict] = {}
        self.creates: list[dict] = []
        self.create_fail: str | None = None
        self.deleted: list[str] = []
        self.requests: list[dict] = []

    def issue(self) -> dict:
        self.serial += 1
        access, refresh = f"access-{self.serial}", f"refresh-{self.serial}"
        self.access.add(access)
        self.refresh_valid.add(refresh)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 3600,
            "scope": "account:read posts:read posts:write offline_access",
            "token_type": "Bearer",
        }

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        if url == TOKEN_URL:
            return self.token(data)
        assert url == API_URL, url
        token = (headers or {}).get("Authorization", "")[7:]
        if token not in self.access or self.revoked:
            return Response(401, {"errors": [{"message": "unauthorized"}]})
        self.requests.append(json)
        query, variables = json["query"], json["variables"]
        if "organizations" in query:
            return Response(
                200, {"data": {"account": {"organizations": [{"id": "org-1", "name": "Mine"}]}}}
            )
        if "channels(" in query:
            return Response(200, {"data": {"channels": CHANNELS}})
        if "createPost" in query:
            return self.create(variables["input"])
        if "deletePost" in query:
            self.deleted.append(variables["input"]["id"])
            self.posts.pop(variables["input"]["id"], None)
            return Response(200, {"data": {"deletePost": {"__typename": "DeletePostSuccess"}}})
        if "posts(" in query:
            nodes = [
                p
                for p in self.posts.values()
                if p["channelId"] in variables["input"]["filter"]["channelIds"]
            ]
            return Response(200, {"data": {"posts": {"edges": [{"node": n} for n in nodes]}}})
        if "post(" in query:
            return Response(200, {"data": {"post": self.posts.get(variables["input"]["id"])}})
        raise AssertionError(query)

    def token(self, data):
        if data["grant_type"] == "authorization_code":
            verifier = data["code_verifier"]
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                data["code"] != "good-code"
                or challenge != self.challenge
                or "client_secret" in data
            ):
                return Response(400, {"error": "invalid_grant"})
            return Response(200, self.issue())
        self.refreshes += 1
        if self.refresh_fail == "timeout":
            # Buffer processed the rotation but the response never arrived.
            self.refresh_valid.discard(data["refresh_token"])
            self.issue()
            raise requests.Timeout()
        if data["refresh_token"] not in self.refresh_valid:
            self.revoked = True  # documented: reuse revokes all tokens for the grant
            self.refresh_valid.clear()
            self.access.clear()
            return Response(400, {"error": "invalid_grant"})
        self.refresh_valid.discard(data["refresh_token"])
        return Response(200, self.issue())

    def create(self, payload):
        mode = self.create_fail
        if mode == "rate":
            return Response(429, {"errors": [{"message": "rate"}]}, {"Retry-After": "60"})
        if mode == "timeout_before":
            raise requests.ConnectionError()
        if mode == "error":
            return Response(
                200,
                {
                    "data": {
                        "createPost": {
                            "__typename": "InvalidInputError",
                            "message": "Video is not supported",
                        }
                    }
                },
            )
        self.creates.append(payload)
        identity = f"post-{len(self.creates)}"
        post = {
            "id": identity,
            "channelId": payload["channelId"],
            "status": "scheduled" if payload["mode"] == "customScheduled" else "sending",
            "schedulingType": "notification"
            if mode == "notification"
            else payload["schedulingType"],
            "shareMode": payload["mode"],
            "dueAt": payload.get("dueAt"),
            "sentAt": None,
            "externalLink": None,
            "error": None,
            "assets": [{"source": payload["assets"][0]["video"]["url"]}],
        }
        self.posts[identity] = post
        if mode == "timeout_after":
            raise requests.Timeout()  # created, but the client never learns it
        return Response(
            200, {"data": {"createPost": {"__typename": "PostActionSuccess", "post": post}}}
        )

    def send(self, identity, link="https://www.tiktok.com/@mytiktok/video/123"):
        self.posts[identity].update(status="sent", sentAt="2030-01-01T09:00:30Z", externalLink=link)


class FakeCloudinary:
    def __init__(self, secret="cloud-secret"):
        self.secret = secret
        self.stored: dict[str, bytes] = {}
        self.uploads = 0
        self.destroyed: list[str] = []
        self.public = True

    def check(self, data):
        params = {k: v for k, v in data.items() if k not in ("api_key", "signature")}
        text = "&".join(f"{k}={params[k]}" for k in sorted(params)) + self.secret
        assert hashlib.sha1(text.encode()).hexdigest() == data["signature"], "bad signature"

    def post(self, url, data=None, files=None, timeout=None):
        self.check(data)
        if url.endswith("/video/upload"):
            self.uploads += 1
            stream = files["file"][1]
            chunks = []
            while block := stream.read(65536):
                chunks.append(block)
            self.stored[data["public_id"]] = b"".join(chunks)
            return Response(
                200,
                {
                    "public_id": data["public_id"],
                    "bytes": len(self.stored[data["public_id"]]),
                    "secure_url": f"https://res.cloudinary.com/demo/video/upload/{data['public_id']}.mp4",
                },
            )
        if url.endswith("/video/destroy"):
            self.destroyed.append(data["public_id"])
            found = self.stored.pop(data["public_id"], None)
            return Response(200, {"result": "ok" if found is not None else "not found"})
        raise AssertionError(url)

    def get(self, url, auth=None, timeout=None):
        assert url.endswith("/usage")
        if auth != ("cloud-key", self.secret):
            return Response(401, {})
        return Response(
            200, {"plan": "Free", "credits": {"usage": 0.4, "limit": 25, "used_percent": 1.6}}
        )

    def head(self, url, allow_redirects=False, timeout=None):
        public_id = url.split("/video/upload/", 1)[1].rsplit(".", 1)[0]
        if not self.public or public_id not in self.stored:
            return Response(404, None)
        return Response(
            200,
            None,
            {"Content-Type": "video/mp4", "Content-Length": str(len(self.stored[public_id]))},
        )
