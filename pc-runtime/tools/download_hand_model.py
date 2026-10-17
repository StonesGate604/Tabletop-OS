"""Explicit, bounded download of the pinned official MediaPipe model asset."""
import argparse
import hashlib
from pathlib import Path
import urllib.request

URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]/"models/hand_landmarker.task")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(20*1024*1024+1)
    if not 100000 < len(data) <= 20*1024*1024:
        raise ValueError("Unexpected model size")
    checksum = hashlib.sha256(data).hexdigest()
    if checksum != SHA256:
        raise ValueError("Model checksum mismatch; refusing changed asset")
    temporary = args.output.with_suffix(".partial")
    temporary.write_bytes(data)
    temporary.replace(args.output)
    args.output.with_suffix(".sha256").write_text(checksum+"\n", encoding="ascii")
    print(f"Saved {args.output} ({len(data)} bytes), SHA256 {checksum}")


if __name__ == "__main__":
    main()
