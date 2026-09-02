#!/usr/bin/env python3
"""Create a length-preserving X5 ADB hash patch and local OTA metadata."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


PATCH_OFFSET = 651_108_823
HASH_SIZE = 64
SEGMENT_SIZE = 100 * 1024 * 1024
ORIGINAL_HASH = "9de0341eb0ac432ecf39b72a0ddf4ac9a5dfb01828c0728dee474a573810a51f"
BUFFER_SIZE = 1024 * 1024


def digest_password() -> str:
    first = getpass.getpass("New ADB password: ")
    second = getpass.getpass("Repeat password: ")
    if not first or first != second:
        raise ValueError("passwords are empty or do not match")
    return hashlib.sha256(first.encode()).hexdigest()


def patch_file(source: Path, output: Path, replacement: str, offset: int = PATCH_OFFSET) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", replacement):
        raise ValueError("replacement must be a lowercase SHA-256 hex digest")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    with source.open("rb") as handle:
        handle.seek(offset)
        if handle.read(HASH_SIZE) != ORIGINAL_HASH.encode():
            raise ValueError("source does not contain the expected hash at the patch offset")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(prefix=output.name + ".", dir=output.parent, delete=False)
    temp_path = Path(temporary.name)
    try:
        with temporary, source.open("rb") as original:
            shutil.copyfileobj(original, temporary, BUFFER_SIZE)
        with temp_path.open("r+b") as patched:
            patched.seek(offset)
            patched.write(replacement.encode())
        if temp_path.stat().st_size != source.stat().st_size:
            raise ValueError("patched copy changed length")
        os.replace(temp_path, output)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def hash_segments(image: Path) -> tuple[str, str, list[dict[str, int | str]]]:
    whole_md5 = hashlib.md5()
    whole_sha256 = hashlib.sha256()
    segments = []
    with image.open("rb") as handle:
        start = 0
        index = 0
        while block := handle.read(SEGMENT_SIZE):
            whole_md5.update(block)
            whole_sha256.update(block)
            end = start + len(block)
            segments.append({
                "num": index,
                "startpos": start,
                "md5": hashlib.md5(block).hexdigest(),
                "endpos": end,
            })
            start = end
            index += 1
    return whole_md5.hexdigest(), whole_sha256.hexdigest(), segments


def update_template(template_path: Path, output_path: Path, image: Path) -> dict:
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")
    if output_path.parent != image.resolve().parent:
        raise ValueError("output template must be beside the patched image")
    document = json.loads(template_path.read_text(encoding="utf-8"))
    md5sum, sha256sum, segments = hash_segments(image)
    document["metadata"]["readyToServe"] = False
    document["metadata"]["image"] = image.name
    version = document["wireResponse"]["data"]["version"]
    version["fileSize"] = image.stat().st_size
    version["md5sum"] = md5sum
    version["sha"] = sha256sum
    version["segmentMd5"] = json.dumps(segments, separators=(",", ":"))
    document["wireResponse"]["data"]["sha256"] = sha256sum
    url = "http://${LOCAL_IP}:14514/" + image.name
    version["deltaUrl"] = version["bakUrl"] = url
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"size": image.stat().st_size, "md5": md5sum, "sha256": sha256sum, "segments": len(segments)}


def self_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "source.bin"
        output = root / "output.bin"
        offset = 17
        source.write_bytes(b"A" * offset + ORIGINAL_HASH.encode() + b"Z" * 11)
        replacement = hashlib.sha256(b"test-password").hexdigest()
        patch_file(source, output, replacement, offset)
        assert source.read_bytes()[offset:offset + HASH_SIZE] == ORIGINAL_HASH.encode()
        assert output.read_bytes()[offset:offset + HASH_SIZE] == replacement.encode()
        assert source.stat().st_size == output.stat().st_size
        md5sum, sha256sum, segments = hash_segments(output)
        assert len(md5sum) == 32 and len(sha256sum) == 64
        assert segments[-1]["endpos"] == output.stat().st_size
    print("self-test: ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--template", type=Path, default=Path(__file__).resolve().parents[1] / "ota-response-template.json")
    parser.add_argument("--output-template", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.source or not args.output:
        parser.error("source and output are required")
    patch_file(args.source.resolve(), args.output.resolve(), digest_password())
    if args.output_template:
        summary = update_template(args.template, args.output_template, args.output.resolve())
    else:
        md5sum, sha256sum, segments = hash_segments(args.output)
        summary = {"size": args.output.stat().st_size, "md5": md5sum, "sha256": sha256sum, "segments": len(segments)}
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
