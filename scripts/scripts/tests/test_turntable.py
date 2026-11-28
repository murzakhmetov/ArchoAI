"""Unit tests for turntable.py (serial control of the turntable) without hardware: a fake firmware
answers on a pseudo-terminal exactly like turntable/turntable.ino does.

Run from scripts/scripts:  python -m unittest discover -s tests -v
"""
import os
import sys
import threading
import time
import tty
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import serial  # noqa: F401
    import turntable
except ImportError as exc:  # pyserial missing: skip instead of failing the whole suite
    turntable = None
    IMPORT_ERROR = exc


class FakeFirmware(threading.Thread):
    """Speaks the firmware protocol on the master side of a pty."""

    def __init__(self, banner=True, steps=200, silent=False):
        super().__init__(daemon=True)
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self.slave = slave
        self.banner, self.steps, self.silent = banner, steps, silent
        self.received = []
        self.stopped = False

    def send(self, text):
        os.write(self.master, (text + "\r\n").encode())

    def run(self):
        if self.banner:
            time.sleep(0.1)
            self.send(f"ARCHOAI-TURNTABLE 1 steps={self.steps}")
        buffer = b""
        while not self.stopped:
            try:
                chunk = os.read(self.master, 256)
            except OSError:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                command = line.decode().strip()
                self.received.append(command)
                if self.silent:
                    continue
                name, _, arg = command.partition(" ")
                if name == "PING":
                    self.send(f"ARCHOAI-TURNTABLE 1 steps={self.steps}")
                elif name == "ROT":
                    self.send(f"OK ROT {arg}" if arg else "ERR ROT needs degrees")
                elif name == "STEPS":
                    self.steps = int(arg)
                    self.send(f"OK STEPS {arg}")
                elif name in ("SPEED", "RAMP", "HOLD", "OFF"):
                    self.send(f"OK {command}")
                else:
                    self.send("ERR unknown command")

    def stop(self):
        self.stopped = True
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


@unittest.skipIf(turntable is None, "pyserial is not installed")
class TurntableProtocolTests(unittest.TestCase):

    def connect(self, **firmware_options):
        firmware = FakeFirmware(**firmware_options)
        firmware.start()
        self.addCleanup(firmware.stop)
        table = turntable.Turntable(port=firmware.port, timeout=0.2)
        self.addCleanup(table.close)
        return firmware, table

    def test_banner_after_the_reset_identifies_the_firmware(self):
        _, table = self.connect()
        self.assertTrue(table.hello.startswith("ARCHOAI-TURNTABLE"))
        self.assertEqual(table.steps_per_turn, 200)

    def test_without_a_banner_a_ping_is_sent(self):
        firmware, table = self.connect(banner=False)
        self.assertIn("PING", firmware.received)
        self.assertTrue(table.hello.startswith("ARCHOAI-TURNTABLE"))

    def test_rotate_sends_degrees_and_waits_for_ok(self):
        firmware, table = self.connect()
        self.assertEqual(table.rotate(60), "OK ROT 60")
        self.assertEqual(table.rotate(-12.5), "OK ROT -12.5")
        self.assertEqual([c for c in firmware.received if c.startswith("ROT")], ["ROT 60", "ROT -12.5"])

    def test_six_steps_of_sixty_degrees_make_one_turn(self):
        firmware, table = self.connect()
        for _ in range(6):
            table.rotate(360 / 6)
        turned = sum(float(c.split()[1]) for c in firmware.received if c.startswith("ROT"))
        self.assertEqual(turned, 360)

    def test_settings_commands(self):
        _, table = self.connect()
        self.assertEqual(table.set_steps_per_turn(3200), "OK STEPS 3200")
        self.assertEqual(table.steps_per_turn, 3200)
        self.assertEqual(table.command("SPEED 22"), "OK SPEED 22")
        self.assertEqual(table.off(), "OK OFF")

    def test_error_answer_raises(self):
        _, table = self.connect()
        with self.assertRaisesRegex(RuntimeError, "ERR unknown command"):
            table.command("JUMP")

    def test_no_answer_raises_instead_of_hanging(self):
        _, silent_table = self.connect(silent=True)
        with self.assertRaisesRegex(RuntimeError, "не ответил"):
            silent_table.command("ROT 60", wait=0.5)

    def test_other_firmware_is_refused(self):
        firmware = FakeFirmware(banner=False, silent=True)
        firmware.start()
        self.addCleanup(firmware.stop)
        with self.assertRaises(RuntimeError):
            turntable.Turntable(port=firmware.port, timeout=0.2)

    def test_port_from_the_environment_wins(self):
        os.environ["TURNTABLE_PORT"] = "/dev/ttyTEST9"
        self.addCleanup(os.environ.pop, "TURNTABLE_PORT", None)
        self.assertEqual(turntable.find_port(), "/dev/ttyTEST9")


if __name__ == "__main__":
    unittest.main()
