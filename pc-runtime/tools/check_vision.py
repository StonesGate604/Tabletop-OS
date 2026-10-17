"""Camera/hand/tag diagnostic only; no device service or motor connection."""
import argparse
from pathlib import Path
import time
from tabletop_os.config import load_config
from tabletop_os.vision import CameraWorker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1]/"config/runtime.json")
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error("seconds must be 0–3600")
    worker = CameraWorker(load_config(args.config))
    worker.start()
    previous = 0
    until = time.monotonic()+args.seconds
    try:
        while time.monotonic() < until:
            result = worker.latest()
            if result.error:
                raise RuntimeError(result.error)
            if result.sample.timestamp > previous:
                previous = result.sample.timestamp
                print(result.sample, "pieces:", result.observations)
            time.sleep(0.05)
    finally:
        worker.close()


if __name__ == "__main__":
    main()
