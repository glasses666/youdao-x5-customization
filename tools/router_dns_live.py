#!/usr/bin/env python3
"""Set or restore the Huawei router DNS used by the X5 OTA lab."""

import argparse
import getpass
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import secrets
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPCookieProcessor, HTTPSHandler, ProxyHandler, Request, build_opener


DEFAULT_BASE = "https://192.168.1.1"
SNAPSHOT = Path(__file__).resolve().parents[1] / ".private" / "router-dns-before-live.json"


class Router:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self.opener = build_opener(
            ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()), HTTPSHandler(context=context)
        )
        self.csrf = {}

    def request(self, path: str, payload: dict | None = None) -> dict:
        headers = {"_ResponseFormat": "JSON", "X-Requested-With": "XMLHttpRequest"}
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json;charset=utf-8"
        with self.opener.open(Request(self.base + path, data=body, headers=headers), timeout=8) as response:
            result = json.loads(response.read())
        if isinstance(result, dict) and result.get("csrf_param") and result.get("csrf_token"):
            self.csrf = {"csrf_param": result["csrf_param"], "csrf_token": result["csrf_token"]}
        return result

    def login(self, password: str) -> None:
        with self.opener.open(self.base + "/html/index.html", timeout=8) as response:
            page = response.read().decode()
        self.csrf = {
            name: re.search(rf'name="{name}" content="([^"]+)"', page).group(1)
            for name in ("csrf_param", "csrf_token")
        }
        first = secrets.token_hex(32)
        nonce = self.request(
            "/api/system/user_login_nonce",
            {"data": {"username": "admin", "firstnonce": first}, "csrf": self.csrf},
        )
        if nonce.get("err") != 0:
            raise RuntimeError(f"nonce rejected: {nonce}")

        salt = bytes.fromhex(nonce["salt"])
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, nonce["iterations"], 32)
        client_key = hmac.new(b"Client Key", salted, hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        auth_message = f'{first},{nonce["servernonce"]},{nonce["servernonce"]}'.encode()
        signature = hmac.new(auth_message, stored_key, hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, signature)).hex()
        result = self.request(
            "/api/system/user_login_proof",
            {"data": {"clientproof": proof, "finalnonce": nonce["servernonce"]}, "csrf": self.csrf},
        )
        server_key = hmac.new(b"Server Key", salted, hashlib.sha256).digest()
        expected = hmac.new(auth_message, server_key, hashlib.sha256).hexdigest()
        if result.get("err") != 0 or not hmac.compare_digest(result.get("serversignature", ""), expected):
            raise RuntimeError("router login proof rejected")


def dns_fields(wan: dict) -> dict:
    return {
        "DNSOverrideAllowed": wan.get("DNSOverrideAllowed"),
        "IPv4DnsServers": wan.get("IPv4DnsServers"),
        "IPv4StaticDnsServers": wan.get("IPv4StaticDnsServers"),
    }


def update(router: Router, wan: dict) -> dict:
    result = router.request(
        "/api/ntwk/wan?type=active",
        {"action": "update", "data": wan, "csrf": router.csrf},
    )
    if result.get("errcode") != 0:
        raise RuntimeError(f"WAN update rejected: {result}")
    return router.request("/api/ntwk/wan?type=active")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("set", "restore"))
    parser.add_argument("--router", default=os.environ.get("X5_ROUTER_BASE", DEFAULT_BASE))
    parser.add_argument("--answer", default="192.168.1.2")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    router = Router(args.router)
    router.login(getpass.getpass("Router password: "))
    wan = router.request("/api/ntwk/wan?type=active")

    if args.action == "set":
        if SNAPSHOT.exists():
            if not args.resume:
                raise SystemExit(f"refusing to overwrite rollback snapshot: {SNAPSHOT}")
        else:
            SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
            snapshot = {"capturedAt": datetime.now(timezone.utc).isoformat(), **dns_fields(wan)}
            descriptor = os.open(SNAPSHOT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2)
                handle.write("\n")
        wan["DNSOverrideAllowed"] = True
        wan["IPv4DnsServers"] = args.answer
    else:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        for name in dns_fields(wan):
            wan[name] = snapshot[name]

    wan.pop("Password", None)
    verified = update(router, wan)
    print(json.dumps(dns_fields(verified), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
