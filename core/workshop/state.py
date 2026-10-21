"""The workshop's runtime state, owned by the bot process (spec 8.2: single
writer). One JSON file: the pinned proxy port, each scope's proxy
credential, approved hosts, the template snapshot, pending approvals.
The dashboard never writes it; its edits arrive as request files (0.14.1).
"""
import hmac
import json
import os
import pathlib
import secrets
from typing import Any, Dict, Optional


class WorkshopState:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self.data: Dict[str, Any] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("credentials", "retired_credentials", "approved_hosts", "pending_approvals"):
            self.data.setdefault(key, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- proxy -------------------------------------------------------------

    @property
    def proxy_port(self) -> Optional[int]:
        return self.data.get("proxy_port")

    @proxy_port.setter
    def proxy_port(self, port: int) -> None:
        self.data["proxy_port"] = port
        self.save()

    def credential(self, scope_key: str) -> Dict[str, str]:
        """This scope's proxy credential, minted on first use."""
        cred = self.data["credentials"].get(scope_key)
        if cred is None:
            cred = {"user": "s" + secrets.token_hex(6), "password": secrets.token_urlsafe(32)}
            self.data["credentials"][scope_key] = cred
            self.save()
        return cred

    def rotate_credentials(self) -> None:
        """Retire every scope's credential (each start, 0.14.1): the next use
        mints a fresh one. Retired ones stay on file per scope, across any
        number of restarts, while a job started under them may still run;
        the workshop prunes them (keep_retired)."""
        for scope_key, cred in self.data["credentials"].items():
            self.data["retired_credentials"].setdefault(scope_key, []).append(cred)
        self.data["credentials"] = {}
        self.save()

    def retired_scope(self, user: str, password: str, scopes) -> Optional[str]:
        """The scope (among scopes) a retired credential belongs to, if it matches."""
        for scope_key in scopes:
            for cred in self.data["retired_credentials"].get(scope_key, []):
                if cred["user"] == user and hmac.compare_digest(password.encode(), cred["password"].encode()):
                    return scope_key
        return None

    def keep_retired(self, scopes) -> None:
        """Drop every retired credential outside scopes."""
        kept = {k: v for k, v in self.data["retired_credentials"].items() if k in scopes}
        if kept != self.data["retired_credentials"]:
            self.data["retired_credentials"] = kept
            self.save()

    def scope_for_user(self, user: str) -> Optional[str]:
        for scope_key, cred in self.data["credentials"].items():
            if cred["user"] == user:
                return scope_key
        return None

    def approved_hosts(self, scope_key: str) -> list:
        return list(self.data["approved_hosts"].get(scope_key, []))

    def approve_host(self, scope_key: str, host: str) -> None:
        hosts = self.data["approved_hosts"].setdefault(scope_key, [])
        if host not in hosts:
            hosts.append(host)
            self.save()

    # --- template and skills ----------------------------------------------

    @property
    def template(self) -> Optional[Dict[str, str]]:
        return self.data.get("template")

    @template.setter
    def template(self, value: Dict[str, str]) -> None:
        self.data["template"] = value
        self.save()

