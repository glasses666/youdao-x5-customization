#!/usr/bin/env python3
"""Offline-first OTA response and Range server for the captured Youdao X5 package."""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = ROOT / "ota-response-template.json"
BUFFER_SIZE = 8 * 1024
SEND_BUFFER_SIZE = 64 * 1024
LIVE_RATE = 512 * 1024
PATCH_START = 651108823
PATCH_END = PATCH_START + 64 - 1
OFFICIAL_IMAGE_URL = os.environ.get("X5_OFFICIAL_IMAGE_URL")
PUBLIC_PATCH_URL = os.environ.get("X5_PUBLIC_PATCH_URL")
PUBLIC_PATCH_TOKEN = os.environ.get("X5_PUBLIC_PATCH_TOKEN")


def range_overlaps_patch(start: int, end: int) -> bool:
    return start <= PATCH_END and end >= PATCH_START
MAX_REQUEST_BODY = 64 * 1024


@dataclass(frozen=True)
class Bundle:
    image: Path
    wire_response: dict
    check_path: str
    device_request: dict
    segments: list[dict]
    ready_to_serve: bool


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_bundle(template_path: Path) -> Bundle:
    template_path = template_path.resolve()
    document = json.loads(template_path.read_text(encoding="utf-8"))
    metadata = document["metadata"]
    wire_response = document["wireResponse"]
    image_name = metadata["image"]
    require(Path(image_name).name == image_name, "metadata.image must be a filename")
    image = (template_path.parent / image_name).resolve()
    require(image.parent == template_path.parent, "image must stay beside the template")
    require(image.is_file(), f"image not found: {image}")
    version = wire_response["data"]["version"]
    segments = json.loads(version["segmentMd5"])
    require(isinstance(segments, list) and segments, "segmentMd5 must contain a list")
    return Bundle(
        image=image,
        wire_response=wire_response,
        check_path=metadata["checkVersionPath"],
        device_request=metadata["deviceRequest"],
        segments=segments,
        ready_to_serve=bool(metadata.get("readyToServe")),
    )


def validate_bundle(bundle: Bundle) -> dict:
    version = bundle.wire_response["data"]["version"]
    size = bundle.image.stat().st_size
    require(version["fileSize"] == size, "fileSize does not match the image")
    require(bundle.segments[0]["startpos"] == 0, "first segment must start at zero")

    whole_md5 = hashlib.md5()
    whole_sha256 = hashlib.sha256()
    offset = 0
    with bundle.image.open("rb") as image:
        for index, segment in enumerate(bundle.segments):
            require(segment["num"] == index, f"segment {index} has the wrong num")
            require(segment["startpos"] == offset, f"segment {index} is not contiguous")
            end = segment["endpos"]
            require(offset < end <= size, f"segment {index} has an invalid endpos")
            segment_md5 = hashlib.md5()
            remaining = end - offset
            while remaining:
                block = image.read(min(BUFFER_SIZE, remaining))
                require(bool(block), f"segment {index} ended early")
                segment_md5.update(block)
                whole_md5.update(block)
                whole_sha256.update(block)
                remaining -= len(block)
            require(
                segment_md5.hexdigest() == segment["md5"],
                f"segment {index} MD5 mismatch",
            )
            offset = end
        require(offset == size and not image.read(1), "segments do not cover the image exactly")

    md5sum = whole_md5.hexdigest()
    sha256sum = whole_sha256.hexdigest()
    require(md5sum == version["md5sum"], "whole-image MD5 mismatch")
    require(sha256sum == version["sha"], "version.sha mismatch")
    require(
        sha256sum == bundle.wire_response["data"]["sha256"],
        "data.sha256 mismatch",
    )
    return {
        "size": size,
        "segments": len(bundle.segments),
        "md5": md5sum,
        "sha256": sha256sum,
    }


def build_wire_response(bundle: Bundle, public_host: str, firmware_port: int) -> bytes:
    response = copy.deepcopy(bundle.wire_response)
    host = f"[{public_host}]" if ":" in public_host else public_host
    url = f"http://{host}:{firmware_port}/{quote(bundle.image.name)}"
    response["data"]["version"]["deltaUrl"] = url
    response["data"]["version"]["bakUrl"] = url
    return json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_demo_bundle(root: Path) -> Bundle:
    image = root / "demo.img"
    image.write_bytes(bytes(range(256)) * 2048)
    payload = image.read_bytes()
    step = 128 * 1024
    segments = [
        {
            "num": index,
            "startpos": start,
            "md5": hashlib.md5(payload[start:end]).hexdigest(),
            "endpos": end,
        }
        for index, start in enumerate(range(0, len(payload), step))
        for end in [min(start + step, len(payload))]
    ]
    sha256sum = hashlib.sha256(payload).hexdigest()
    version = {
        "segmentMd5": json.dumps(segments, separators=(",", ":")),
        "bakUrl": "",
        "deltaUrl": "",
        "fileSize": len(payload),
        "md5sum": hashlib.md5(payload).hexdigest(),
        "sha": sha256sum,
    }
    return Bundle(
        image=image,
        wire_response={"status": 1000, "data": {"sha256": sha256sum, "version": version}},
        check_path="/product/demo/device/ota/checkVersion",
        device_request={"mid": "demo", "productId": "demo", "version": "0", "networkType": "WIFI"},
        segments=segments,
        ready_to_serve=False,
    )


def valid_device_request(bundle: Bundle, body: bytes) -> bool:
    try:
        request = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    expected = bundle.device_request
    for key in ("mid", "productId", "version", "networkType"):
        if request.get(key) != expected.get(key):
            return False
    return isinstance(request.get("timestamp"), int) and bool(request.get("sign"))


def send_empty(handler: BaseHTTPRequestHandler, status: int, **headers: str) -> None:
    handler.send_response(status)
    for name, value in headers.items():
        handler.send_header(name.replace("_", "-"), value)
    handler.send_header("Content-Length", "0")
    handler.send_header("Connection", "close")
    handler.end_headers()


def make_api_handler(bundle: Bundle, response_body: bytes, allowed_clients: frozenset[str]):
    class ApiHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if self.client_address[0] not in allowed_clients:
                send_empty(self, 403)
                return
            if urlsplit(self.path).path != bundle.check_path:
                send_empty(self, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                send_empty(self, 400)
                return
            if not 0 < length <= MAX_REQUEST_BODY:
                send_empty(self, 413)
                return
            body = self.rfile.read(length)
            if len(body) != length or not valid_device_request(bundle, body):
                send_empty(self, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json;charset=UTF-8")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)

        def do_GET(self) -> None:
            status = 405 if urlsplit(self.path).path == bundle.check_path else 404
            send_empty(self, status, Allow="POST" if status == 405 else "")

        def log_message(self, *_args) -> None:
            pass

    return ApiHandler


def parse_range(value: str | None, size: int) -> tuple[int, int, bool] | None:
    if value is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        suffix = int(last)
        if suffix <= 0:
            return None
        return max(size - suffix, 0), size - 1, True
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or start > end:
        return None
    return start, min(end, size - 1), True


def make_firmware_handler(bundle: Bundle, allowed_clients: frozenset[str]):
    expected_path = f"/{bundle.image.name}"
    size = bundle.image.stat().st_size

    class FirmwareHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SEND_BUFFER_SIZE)

        def do_HEAD(self) -> None:
            self._serve(send_body=False)

        def do_GET(self) -> None:
            self._serve(send_body=True)

        def _serve(self, send_body: bool) -> None:
            if self.client_address[0] not in allowed_clients:
                send_empty(self, 403)
                return
            request_url = urlsplit(self.path)
            if unquote(request_url.path) != expected_path:
                send_empty(self, 404)
                return
            if (
                PUBLIC_PATCH_URL
                and PUBLIC_PATCH_TOKEN
                and self.client_address[0] == "127.0.0.1"
                and request_url.query != PUBLIC_PATCH_TOKEN
            ):
                send_empty(self, 403)
                return
            byte_range = parse_range(self.headers.get("Range"), size)
            if byte_range is None:
                send_empty(self, 416, Content_Range=f"bytes */{size}")
                return
            start, end, partial = byte_range
            length = end - start + 1
            redirect_url = None
            if send_body and partial and self.client_address[0] != "127.0.0.1":
                redirect_url = PUBLIC_PATCH_URL if range_overlaps_patch(start, end) else OFFICIAL_IMAGE_URL
            if redirect_url:
                self.send_response(302)
                self.send_header("Location", redirect_url)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "application/octet-stream;charset=UTF-8")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Last-Modified", "Tue, 07 Apr 2026 07:04:33 GMT")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            if not send_body:
                return
            try:
                with bundle.image.open("rb") as image:
                    image.seek(start)
                    remaining = length
                    while remaining:
                        block = image.read(min(BUFFER_SIZE, remaining))
                        if not block:
                            break
                        write_started = time.monotonic()
                        self.wfile.write(block)
                        remaining -= len(block)
                        if self.client_address[0] != "127.0.0.1":
                            delay = len(block) / LIVE_RATE - (time.monotonic() - write_started)
                            if delay > 0:
                                time.sleep(delay)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args) -> None:
            pass

    return FirmwareHandler


def start_servers(
    bundle: Bundle,
    bind: str,
    api_port: int,
    firmware_port: int,
    public_host: str,
    allowed_clients: frozenset[str],
):
    firmware_server = ThreadingHTTPServer(
        (bind, firmware_port), make_firmware_handler(bundle, allowed_clients)
    )
    actual_firmware_port = firmware_server.server_address[1]
    response_body = build_wire_response(bundle, public_host, actual_firmware_port)
    try:
        api_server = ThreadingHTTPServer(
            (bind, api_port), make_api_handler(bundle, response_body, allowed_clients)
        )
    except Exception:
        firmware_server.server_close()
        raise
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (firmware_server, api_server)
    ]
    for thread in threads:
        thread.start()
    return api_server, firmware_server, threads


def stop_servers(api_server, firmware_server, threads) -> None:
    for server in (api_server, firmware_server):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=2)


def request_json(port: int, method: str, path: str, body: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if encoded is not None else {}
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    status = response.status
    response_headers = dict(response.getheaders())
    connection.close()
    return status, response_headers, payload


def request_range(
    port: int,
    path: str,
    start: int,
    end: int,
    total_size: int,
    expected_md5: str,
) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    connection.request("GET", path, headers={"Range": f"bytes={start}-{end - 1}"})
    response = connection.getresponse()
    require(response.status == 206, f"range {start}-{end} returned {response.status}")
    require(
        response.getheader("Content-Range") == f"bytes {start}-{end - 1}/{total_size}",
        f"range {start}-{end} returned the wrong Content-Range",
    )
    require(
        int(response.getheader("Content-Length")) == end - start,
        f"range {start}-{end} returned the wrong Content-Length",
    )
    require(response.getheader("Accept-Ranges") == "bytes", "Range response lacks Accept-Ranges")
    digest = hashlib.md5()
    received = 0
    while block := response.read(BUFFER_SIZE):
        digest.update(block)
        received += len(block)
    connection.close()
    require(received == end - start, f"range {start}-{end} returned {received} bytes")
    require(digest.hexdigest() == expected_md5, f"range {start}-{end} MD5 mismatch")
    return received


def offline_rehearsal(bundle: Bundle) -> None:
    require(not range_overlaps_patch(0, PATCH_START - 1), "pre-patch range overlap error")
    require(range_overlaps_patch(PATCH_START, PATCH_END), "patch range overlap error")
    require(not range_overlaps_patch(PATCH_END + 1, bundle.image.stat().st_size - 1), "post-patch range overlap error")
    summary = validate_bundle(bundle)
    api_server = firmware_server = threads = None
    try:
        api_server, firmware_server, threads = start_servers(
            bundle,
            bind="127.0.0.1",
            api_port=0,
            firmware_port=0,
            public_host="127.0.0.1",
            allowed_clients=frozenset({"127.0.0.1"}),
        )
        api_port = api_server.server_address[1]
        firmware_port = firmware_server.server_address[1]
        request = {
            "timestamp": 1,
            "sign": "offline-rehearsal",
            "mid": bundle.device_request["mid"],
            "productId": bundle.device_request["productId"],
            "version": bundle.device_request["version"],
            "networkType": bundle.device_request["networkType"],
        }
        status, _, payload = request_json(api_port, "POST", bundle.check_path, request)
        require(status == 200, f"OTA POST returned {status}")
        response = json.loads(payload)
        require(response["status"] == 1000, "OTA response status is not 1000")
        expected_url = f"http://127.0.0.1:{firmware_port}/{quote(bundle.image.name)}"
        version = response["data"]["version"]
        require(version["deltaUrl"] == expected_url, "deltaUrl was not rendered")
        require(version["bakUrl"] == expected_url, "bakUrl was not rendered")

        bad_request = dict(request, mid="wrong-device")
        require(
            request_json(api_port, "POST", bundle.check_path, bad_request)[0] == 400,
            "wrong device request was not rejected",
        )
        require(
            request_json(api_port, "GET", bundle.check_path)[0] == 405,
            "GET check request was not rejected",
        )

        file_path = f"/{quote(bundle.image.name)}"
        connection = http.client.HTTPConnection("127.0.0.1", firmware_port, timeout=10)
        connection.request("HEAD", file_path)
        head = connection.getresponse()
        require(head.status == 200, f"firmware HEAD returned {head.status}")
        require(int(head.getheader("Content-Length")) == summary["size"], "wrong HEAD size")
        require(head.getheader("Accept-Ranges") == "bytes", "HEAD lacks Accept-Ranges")
        head.read()
        connection.close()

        received = 0
        for segment in bundle.segments:
            received += request_range(
                firmware_port,
                file_path,
                segment["startpos"],
                segment["endpos"],
                summary["size"],
                segment["md5"],
            )
        require(received == summary["size"], "Range rehearsal did not cover the whole image")

        connection = http.client.HTTPConnection("127.0.0.1", firmware_port, timeout=10)
        connection.request("GET", file_path, headers={"Range": f"bytes={summary['size']}-"})
        invalid = connection.getresponse()
        require(invalid.status == 416, f"invalid range returned {invalid.status}")
        require(
            invalid.getheader("Content-Range") == f"bytes */{summary['size']}",
            "416 response has the wrong Content-Range",
        )
        invalid.read()
        connection.close()

        print(f"image: {summary['size']} bytes")
        print(f"md5: {summary['md5']}")
        print(f"sha256: {summary['sha256']}")
        print(f"segments: {summary['segments']}/{summary['segments']} via HTTP Range")
        print("api: POST=200 wrong-device=400 GET=405")
        print("firmware: HEAD=200 Range=206 invalid-Range=416")
        print("offline rehearsal: PASS")
    finally:
        if api_server is not None:
            stop_servers(api_server, firmware_server, threads)


def literal_ip(value: str) -> str:
    return str(ipaddress.ip_address(value))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--verify-image", action="store_true")
    parser.add_argument("--bind", type=literal_ip, default="127.0.0.1")
    parser.add_argument("--public-host", type=literal_ip, default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=18080)
    parser.add_argument("--firmware-port", type=int, default=14514)
    parser.add_argument("--allow-client", action="append", type=literal_ip)
    parser.add_argument("--arm-live", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        with tempfile.TemporaryDirectory() as directory:
            offline_rehearsal(make_demo_bundle(Path(directory)))
        return

    bundle = load_bundle(args.template)
    if args.verify_image:
        offline_rehearsal(bundle)
        return

    allowed = frozenset(args.allow_client or ["127.0.0.1"])
    addresses = {args.bind, args.public_host, *allowed}
    live_surface = any(not ipaddress.ip_address(address).is_loopback for address in addresses)
    if live_surface and not args.arm_live:
        raise SystemExit("refusing LAN exposure without --arm-live")
    if live_surface and not bundle.ready_to_serve:
        raise SystemExit("refusing LAN exposure while metadata.readyToServe is false")

    summary = validate_bundle(bundle)
    api_server, firmware_server, threads = start_servers(
        bundle,
        args.bind,
        args.api_port,
        args.firmware_port,
        args.public_host,
        allowed,
    )
    print(
        f"ready api={args.bind}:{api_server.server_address[1]} "
        f"firmware={args.bind}:{firmware_server.server_address[1]} "
        f"clients={','.join(sorted(allowed))} size={summary['size']}",
        flush=True,
    )
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop_servers(api_server, firmware_server, threads)


if __name__ == "__main__":
    main()
