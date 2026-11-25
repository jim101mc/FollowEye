from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import keyring
from cryptography.fernet import Fernet, InvalidToken

KEYRING_SERVICE = "FollowEye"
_VAULT_KEY_NAME = "vault-key"


def get_secret(name: str) -> str | None:
    return keyring.get_password(KEYRING_SERVICE, name)


def set_secret(name: str, value: str | None) -> None:
    if value:
        keyring.set_password(KEYRING_SERVICE, name, value)
    elif keyring.get_password(KEYRING_SERVICE, name) is not None:
        keyring.delete_password(KEYRING_SERVICE, name)


class Vault:
    """Symmetric encryption whose key lives in Windows Credential Manager (DPAPI-protected)."""

    def __init__(self, key: bytes | None = None):
        if key is None:
            stored = get_secret(_VAULT_KEY_NAME)
            if stored is None:
                stored = Fernet.generate_key().decode()
                set_secret(_VAULT_KEY_NAME, stored)
            key = stored.encode()
        self._fernet = Fernet(key)

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        return self._fernet.decrypt(token)


class EncryptedJsonFile:
    def __init__(self, path: Path, vault: Vault):
        self.path = path
        self._vault = vault

    def load(self, default: Any = None) -> Any:
        if not self.path.exists():
            return default
        try:
            return json.loads(self._vault.decrypt(self.path.read_bytes()))
        except InvalidToken:
            return default

    def save(self, value: Any) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._vault.encrypt(json.dumps(value).encode()))
        tmp.replace(self.path)


class HistoryLog:
    """Append-only encrypted log: one Fernet token per line, each holding one JSON record."""

    def __init__(self, path: Path, vault: Vault, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._vault = vault
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> str:
        record = {"id": uuid.uuid4().hex, "time": time.time(), **record}
        line = self._vault.encrypt(json.dumps(record).encode()) + b"\n"
        with self._lock, self.path.open("ab") as f:
            f.write(line)
        return record["id"]

    def update(self, record_id: str, **changes: Any) -> None:
        with self._lock:
            records = self._read()
            for r in records:
                if r["id"] == record_id:
                    r.update(changes)
            self._write(records)

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read()

    def delete(self, record_ids: set[str]) -> None:
        with self._lock:
            self._write([r for r in self._read() if r["id"] not in record_ids])

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)

    def purge_old(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.retention_days * 86400
        with self._lock:
            records = self._read()
            kept = [r for r in records if r["time"] >= cutoff]
            if len(kept) != len(records):
                self._write(kept)
            return len(records) - len(kept)

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_bytes().splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(self._vault.decrypt(line)))
            except InvalidToken:
                continue  # written with a different key, e.g. after a reset of Credential Manager
        return out

    def _write(self, records: list[dict[str, Any]]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            for r in records:
                f.write(self._vault.encrypt(json.dumps(r).encode()) + b"\n")
        tmp.replace(self.path)
