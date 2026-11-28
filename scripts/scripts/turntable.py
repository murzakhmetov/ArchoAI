"""Control of the ArchoAI turntable (Arduino Uno + CNC Shield V3 + NEMA 17, firmware turntable/turntable.ino).

    python turntable.py ping            # firmware answer, steps per turn
    python turntable.py rot 60          # turn the table by 60 degrees
    python turntable.py steps 3200      # motor steps for one full table turn (saved on the Arduino)
    python turntable.py speed 20        # cruise speed, table turns per minute (until the next reset)
    python turntable.py ramp 0.3        # seconds to speed up / slow down (until the next reset)
    python turntable.py hold on         # keep the motor current on between moves (default off: silent)
    python turntable.py off             # motor current off

The port comes from TURNTABLE_PORT, otherwise the first Arduino-like USB serial device is used.
"""
import glob
import os
import sys
import time

import serial

BAUD = 115200


def find_port():
    configured = (os.getenv("TURNTABLE_PORT") or "").strip()
    if configured:
        return configured
    for pattern in ("/dev/serial/by-id/*Arduino*", "/dev/serial/by-id/*USB_SER*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    raise RuntimeError("Поворотный стол не найден: подключите Arduino по USB или задайте TURNTABLE_PORT")


class Turntable:
    """One open connection to the turntable. Opening it resets the Uno, which takes about 2 seconds."""

    def __init__(self, port=None, timeout=120.0):
        self.port = port or find_port()
        try:
            # exclusive: a second scanner gets a clear error at once instead of sharing the port and
            # waiting for answers the first one reads
            self.serial = serial.Serial(self.port, BAUD, timeout=0.5, exclusive=True)
        except serial.SerialException as exc:
            if "lock" in str(exc).lower() or "busy" in str(exc).lower() or getattr(exc, "errno", None) == 11:
                raise RuntimeError(f"Поворотный стол ({self.port}) занят: возможно, сканер уже запущен в другом "
                                   "окне терминала. Закройте его (Q) и запустите снова.") from None
            raise RuntimeError(f"Не удалось открыть поворотный стол {self.port}: {exc}") from None
        self.hello = self._read_line(wait=5.0)  # the banner printed after the reset
        if not self.hello.startswith("ARCHOAI-TURNTABLE"):
            try:
                self.hello = self.command("PING", wait=5.0)  # no banner: ask once, fail fast
            except RuntimeError:
                self.hello = ""
        if not self.hello.startswith("ARCHOAI-TURNTABLE"):
            raise RuntimeError(f"На {self.port} не прошивка поворотного стола (ответ: {self.hello!r}). "
                               "Прошейте её: bash turntable/flash.sh")

    def _read_line(self, wait):
        end = time.monotonic() + wait
        while time.monotonic() < end:
            # each read waits at most 0.5 s, so ``wait`` is the real limit (not the port timeout)
            self.serial.timeout = max(0.05, min(0.5, end - time.monotonic()))
            line = self.serial.readline().decode("ascii", "replace").strip()
            if line:
                return line
        return ""

    def command(self, text, wait=120.0):
        self.serial.reset_input_buffer()
        self.serial.write((text + "\n").encode("ascii"))
        answer = self._read_line(wait)
        if not answer:
            raise RuntimeError(f"Поворотный стол не ответил на {text!r}")
        if answer.startswith("ERR"):
            raise RuntimeError(f"Поворотный стол: {answer}")
        return answer

    @property
    def steps_per_turn(self):
        return int(self.command("PING").split("steps=")[1])

    def rotate(self, degrees):
        """Turns the table and returns when the move is finished."""
        return self.command(f"ROT {degrees:g}")

    def set_steps_per_turn(self, steps):
        return self.command(f"STEPS {int(steps)}")

    def off(self):
        return self.command("OFF")

    def close(self):
        self.serial.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    with Turntable() as table:
        action = argv[0].lower()
        if action == "ping":
            print(table.port, table.command("PING"))
        elif action == "rot":
            print(table.rotate(float(argv[1])))
        elif action == "steps":
            print(table.set_steps_per_turn(int(argv[1])))
        elif action in ("speed", "ramp", "hold", "profile"):
            print(table.command(f"{action.upper()} {argv[1]}"))
        elif action == "off":
            print(table.off())
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
