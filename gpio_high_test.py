#!/usr/bin/env python3
"""
Set one Raspberry Pi GPIO pin HIGH from an interactive CLI prompt.

Uses BCM GPIO numbering, not physical header pin numbering.
Install on Raspberry Pi OS if needed:
  sudo apt update
  sudo apt install -y python3-gpiozero python3-lgpio

Run:
  python3 gpio_high_test.py
  python3 gpio_high_test.py --allow 17,27,22
"""

from __future__ import annotations

import argparse
import signal
import sys

from gpiozero import OutputDevice


# GPIO pins exposed on the common 40-pin Raspberry Pi header.
# GPIO0 and GPIO1 are intentionally omitted because they are normally reserved
# for HAT ID EEPROM on physical pins 27 and 28.
DEFAULT_BCM_PINS = [
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
]


current_device: OutputDevice | None = None
current_pin: int | None = None


def parse_pin_list(value: str) -> set[int]:
    try:
        pins = {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("GPIO pins must be comma-separated BCM numbers") from exc

    if not pins:
        raise argparse.ArgumentTypeError("at least one GPIO pin is required")
    return pins


def all_low() -> None:
    global current_device, current_pin

    if current_device is None:
        return

    try:
        current_device.off()
        current_device.close()
    except Exception:
        pass
    finally:
        current_device = None
        current_pin = None


def set_high(pin: int) -> None:
    global current_device, current_pin

    if current_pin == pin:
        print(f"GPIO{pin} is already HIGH.")
        return

    all_low()
    current_device = OutputDevice(pin, active_high=True, initial_value=False)
    current_pin = pin
    current_device.on()
    print(f"GPIO{pin} HIGH. Measure GPIO{pin} against GND.")


def handle_exit(signum, frame) -> None:  # noqa: ANN001
    print("\nStopping and setting current GPIO LOW...")
    all_low()
    sys.exit(0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Type a BCM GPIO number to set that Raspberry Pi GPIO HIGH."
    )
    parser.add_argument(
        "--allow",
        type=parse_pin_list,
        default=set(DEFAULT_BCM_PINS),
        help="Comma-separated BCM GPIO pins allowed for testing. Example: --allow 17,27,22",
    )
    parser.add_argument(
        "--active-high-voltage",
        default="about 3.3 V",
        help="Text shown in prompts; Raspberry Pi GPIO HIGH is normally about 3.3 V.",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    allowed_pins = set(args.allow)

    print("Allowed BCM GPIO pins:", ", ".join(str(pin) for pin in sorted(allowed_pins)))
    print("Type a BCM GPIO number to set it HIGH.")
    print("Type 'off' to set the current GPIO LOW. Type 'q' or 'quit' to exit.")
    print("GPIO HIGH should read", args.active_high_voltage, "against any GND pin.")
    print("Only one GPIO will be HIGH at a time.\n")

    try:
        while True:
            command = input("GPIO> ").strip().lower()

            if command in {"q", "quit", "exit"}:
                break
            if command in {"off", "low", "0"}:
                all_low()
                print("Current GPIO LOW.")
                continue

            try:
                pin = int(command)
            except ValueError:
                print("Please enter a BCM GPIO number, 'off', or 'q'.")
                continue

            if pin not in allowed_pins:
                print(f"GPIO{pin} is not in the allowed list.")
                continue

            set_high(pin)

        print("\nDone. Current GPIO is LOW.")
        return 0
    finally:
        all_low()


if __name__ == "__main__":
    raise SystemExit(main())
