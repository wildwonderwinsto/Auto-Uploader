"""Explicit OS secret-storage boundary; never select a fallback backend."""

import json
import sys
from typing import Protocol

from .auth import SCOPE, AccountError, Tokens


class CredentialStore(Protocol):
    def get(self, key: str) -> Tokens | None: ...
    def put(self, key: str, tokens: Tokens) -> None: ...
    def delete(self, key: str) -> None: ...


class KeychainStore:
    def __init__(self, service: str = "ShortsPublisher.YouTube") -> None:
        self.service = service

    def backend(self):  # type: ignore[no-untyped-def]
        if sys.platform != "darwin":
            raise AccountError(
                "Secure account storage currently requires macOS Keychain. No plaintext fallback is available.",
                "storage",
            )
        from keyring.backends.macOS import Keyring

        return Keyring()

    def get(self, key: str) -> Tokens | None:
        try:
            value = self.backend().get_password(self.service, key)
            if value is None:
                return None
            data = json.loads(value)
            # An expired access token is normal; refresh it after reading.
            access, refresh, expiry = (
                data["access_token"],
                data["refresh_token"],
                data["expires_at"],
            )
            if (
                not isinstance(access, str)
                or not isinstance(refresh, str)
                or not refresh
                or not isinstance(expiry, (int, float))
            ):
                raise ValueError
            return Tokens(
                access, refresh, float(expiry), frozenset(data.get("scope", SCOPE).split())
            )
        except Exception:
            raise AccountError(
                "Cannot read authorization from macOS Keychain. Unlock or allow Keychain access, then retry. Stored data was not reset.",
                "storage",
            ) from None

    def put(self, key: str, tokens: Tokens) -> None:
        try:
            self.backend().set_password(self.service, key, tokens.encode())
        except Exception:
            raise AccountError(
                "Cannot save authorization in macOS Keychain. Allow Keychain access and retry. No plaintext copy was made.",
                "storage",
            ) from None

    def delete(self, key: str) -> None:
        try:
            backend = self.backend()
            if backend.get_password(self.service, key) is not None:
                backend.delete_password(self.service, key)
        except Exception:
            raise AccountError(
                "Cannot remove authorization from macOS Keychain. Allow access and retry Disconnect; removal is not confirmed.",
                "storage",
            ) from None


class SessionStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeychainSessions:
    """Resumable URLs are bearer capabilities, kept outside SQLite and exports."""

    def __init__(self, service: str = "ShortsPublisher.UploadSessions") -> None:
        self.store = KeychainStore(service)

    def get(self, key: str) -> str | None:
        try:
            return self.store.backend().get_password(self.store.service, key)
        except Exception:
            raise AccountError(
                "Cannot read the upload session from Keychain. Unlock Keychain and retry.",
                "storage",
            ) from None

    def put(self, key: str, value: str) -> None:
        try:
            self.store.backend().set_password(self.store.service, key, value)
        except Exception:
            raise AccountError(
                "Cannot securely save the upload session. No video bytes were sent; reconciliation is required.",
                "storage",
            ) from None

    def delete(self, key: str) -> None:
        self.store.delete(key)


class KeychainSecrets:
    """Opaque string secrets (Buffer sign-in, media-host API secret) in their own namespace."""

    def __init__(self, service: str) -> None:
        self.store = KeychainStore(service)

    def get(self, key: str) -> str | None:
        try:
            return self.store.backend().get_password(self.store.service, key)
        except Exception:
            raise AccountError(
                "Cannot read a saved credential from macOS Keychain. Unlock Keychain and retry.",
                "storage",
            ) from None

    def put(self, key: str, value: str) -> None:
        try:
            self.store.backend().set_password(self.store.service, key, value)
        except Exception:
            raise AccountError(
                "Cannot save the credential in macOS Keychain. Nothing was stored in plaintext.",
                "storage",
            ) from None

    def delete(self, key: str) -> None:
        self.store.delete(key)
