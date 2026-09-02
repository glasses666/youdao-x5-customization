#!/usr/bin/env python3
"""DNS override for the X5 OTA API; all other queries are forwarded."""

import argparse
import ipaddress
import socket

import capture_check_version as capture


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer", type=ipaddress.ip_address, default="192.168.1.2")
    parser.add_argument("--target", default=capture.TARGET)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    capture.LAN_IP = str(args.answer)
    capture.TARGET = args.target.lower().rstrip(".")
    capture.self_test()
    if args.self_test:
        print(f"self-test: ok {capture.TARGET}->{capture.LAN_IP}")
        return

    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 53))
    print(
        f"ready dns={capture.TARGET}->{capture.LAN_IP} "
        f"upstream={','.join(capture.UPSTREAM_DNS)}",
        flush=True,
    )
    capture.dns_loop(server)


if __name__ == "__main__":
    main()
