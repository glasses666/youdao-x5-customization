#!/usr/bin/env python3
"""Capture one X5 OTA check request and always report no update."""

import argparse
import base64
import ipaddress
import json
import os
import socket
import struct
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEVICE_IP = "192.168.1.100"
LAN_IP = "192.168.1.2"
TARGET = "iotapi.abupdate.com"
UPSTREAM_DNS = ("8.8.8.8", "223.5.5.5", "1.1.1.1")
OUTPUT = os.path.join(os.path.dirname(__file__), "capture", "check-version-request.json")
MAX_BODY = 64 * 1024
NO_UPDATE = json.dumps(
    {"status": 2101, "msg": "no new version", "data": None},
    separators=(",", ":"),
).encode()
_capture_lock = threading.Lock()


def parse_question(packet):
    if len(packet) < 17 or struct.unpack("!H", packet[4:6])[0] != 1:
        raise ValueError("unsupported DNS question count")
    labels, pos = [], 12
    while True:
        if pos >= len(packet):
            raise ValueError("truncated DNS name")
        size = packet[pos]
        pos += 1
        if size == 0:
            break
        if size > 63 or pos + size > len(packet):
            raise ValueError("invalid DNS label")
        labels.append(packet[pos : pos + size].decode("ascii"))
        pos += size
    if pos + 4 > len(packet):
        raise ValueError("truncated DNS question")
    qtype, qclass = struct.unpack("!HH", packet[pos : pos + 4])
    return ".".join(labels).lower(), qtype, qclass, pos + 4


def local_dns_reply(packet):
    name, qtype, qclass, question_end = parse_question(packet)
    if name != TARGET:
        return None
    flags = b"\x81\x80"
    question = packet[12:question_end]
    if qtype == 1 and qclass == 1:
        header = packet[:2] + flags + b"\x00\x01\x00\x01\x00\x00\x00\x00"
        answer = (
            b"\xc0\x0c\x00\x01\x00\x01"
            + struct.pack("!IH", 1, 4)
            + socket.inet_aton(LAN_IP)
        )
        return header + question + answer
    header = packet[:2] + flags + b"\x00\x01\x00\x00\x00\x00\x00\x00"
    return header + question


def forward_dns(query):
    last_error = None
    for address in UPSTREAM_DNS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                upstream.settimeout(1)
                upstream.sendto(query, (address, 53))
                return upstream.recv(65535)
        except OSError as error:
            last_error = error
    raise last_error or OSError("no upstream DNS configured")


def dns_loop(server):
    with server:
        while True:
            query, client = server.recvfrom(4096)
            try:
                reply = local_dns_reply(query)
                if reply is None:
                    reply = forward_dns(query)
                server.sendto(reply, client)
            except (OSError, UnicodeDecodeError, ValueError, struct.error):
                continue


def save_request(handler, body):
    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = None
    parsed = None
    if body_text is not None:
        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError:
            pass
    record = {
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "client": handler.client_address[0],
        "method": handler.command,
        "host": handler.headers.get("Host"),
        "path": handler.path,
        "headers": dict(handler.headers.items()),
        "bodyUtf8": body_text,
        "bodyBase64": base64.b64encode(body).decode("ascii"),
        "bodyJson": parsed,
        "response": json.loads(NO_UPDATE),
    }
    with _capture_lock:
        if os.path.exists(OUTPUT):
            return False
        descriptor = os.open(OUTPUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if os.geteuid() == 0:
            directory = os.stat(os.path.dirname(OUTPUT))
            os.chown(OUTPUT, directory.st_uid, directory.st_gid)
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        if self.client_address[0] != DEVICE_IP:
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400)
            return
        if not 0 <= length <= MAX_BODY:
            self.send_error(413)
            return
        body = self.rfile.read(length)
        is_new = save_request(self, body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(NO_UPDATE)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(NO_UPDATE)
        print(f"captured={is_new} client={self.client_address[0]} path={self.path}", flush=True)

    def do_GET(self):
        self.send_error(404)

    def log_message(self, *_):
        pass


def self_test():
    query = (
        bytes.fromhex("123401000001000000000000")
        + b"\x06iotapi\x08abupdate\x03com\x00\x00\x01\x00\x01"
    )
    reply = local_dns_reply(query)
    assert reply is not None and reply[:2] == b"\x12\x34"
    assert reply[-4:] == socket.inet_aton(LAN_IP)
    aaaa = query[:-4] + b"\x00\x1c\x00\x01"
    assert local_dns_reply(aaaa)[6:8] == b"\x00\x00"
    other = query.replace(b"iotapi", b"foobar")
    assert local_dns_reply(other) is None
    assert json.loads(NO_UPDATE)["status"] == 2101


def main():
    global DEVICE_IP, LAN_IP, TARGET, OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--device-ip", type=ipaddress.ip_address, default=DEVICE_IP)
    parser.add_argument("--answer", type=ipaddress.ip_address, default=LAN_IP)
    parser.add_argument("--target", default=TARGET)
    parser.add_argument("--output", default=OUTPUT)
    args = parser.parse_args()
    DEVICE_IP = str(args.device_ip)
    LAN_IP = str(args.answer)
    TARGET = args.target.lower().rstrip(".")
    OUTPUT = os.path.abspath(args.output)
    self_test()
    if args.self_test:
        print("self-test: ok")
        return
    if os.path.exists(OUTPUT):
        raise SystemExit(f"refusing to overwrite {OUTPUT}")
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    dns_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    dns_server.bind(("0.0.0.0", 53))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", 80), Handler)
    except Exception:
        dns_server.close()
        raise
    threading.Thread(target=dns_loop, args=(dns_server,), daemon=True).start()
    print(f"ready dns={TARGET}->{LAN_IP} http=:80 output={OUTPUT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
