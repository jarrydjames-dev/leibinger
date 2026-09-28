"""
Leibinger JET2neo - sensor-triggered unique serial printing (Avis Labs)

How it works
------------
The PRINTER owns the trigger, not this script. The external photo-eye is wired
to the printer's product-detection (print-go) input. Each time the sensor fires,
the printer prints whatever is currently in field 1 and its print counter (?PC)
goes up by one.

This script:
  1. Reads the print counter (baseline).
  2. Loads the next serial into field 1 (=ST1...).
  3. Waits - indefinitely - until the counter goes up by exactly 1
     (= one product passed the sensor and was printed).
  4. Logs the serial as printed, then loads the next one.

It never advances on a timer. If the counter cannot be read, it stops instead of
guessing, so a serial is never skipped or silently re-used.

Usage
-----
    python leibinger_serial_printer.py                  # uses defaults below
    python leibinger_serial_printer.py --file Data.xlsx --ip 192.168.1.100
    python leibinger_serial_printer.py --test-connection  # show raw ?PC reply
"""

import argparse
import csv
import datetime as dt
import os
import re
import socket
import sys
import time

import pandas as pd

# ==========================================
# 1. CONFIGURATION
# ==========================================
PRINTER_IP = "192.168.1.100"   # Printer IP address
PRINTER_PORT = 2201            # Leibinger TCP interface port
INPUT_FILE = "Data.csv"        # .csv, .xlsx or .xls - serials in the first column
PRINT_LOG = "print_log.csv"    # Every printed serial is appended here (audit + resume)

# Protocol - check these against the interface manual for your firmware
STX = b"\x02"
CR = b"\x0d"
FRAME_END = re.compile(rb"[\r\n\x03]+")  # replies may end in CR, LF or ETX
CMD_GET_COUNTER = "?PC"        # Query print counter
REPLY_COUNTER = "=PC"          # Counter reply prefix, e.g. '=PC104'
CMD_SET_FIELD = "=ST1"         # Load text into field 1

POLL_INTERVAL = 0.05           # Seconds between counter polls while waiting for a product
REPLY_TIMEOUT = 2.0            # Seconds to wait for a reply to a single query
MAX_QUERY_FAILURES = 5         # Consecutive failed counter reads before stopping


class PrinterError(Exception):
    pass


# ==========================================
# 2. PRINTER CONNECTION
# ==========================================
class LeibingerPrinter:
    """TCP connection that reads whole STX...CR frames, so replies never get mixed up."""

    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.sock = None
        self._buffer = b""

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), timeout=5.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    @staticmethod
    def _frame(payload: str) -> bytes:
        return STX + payload.strip().encode("ascii") + CR

    def send(self, payload: str):
        self.sock.sendall(self._frame(payload))

    def _read_frame(self, deadline: float):
        """Return the next complete reply (without STX/terminator), or None on timeout."""
        while not FRAME_END.search(self._buffer):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(1024)
            except socket.timeout:
                return None
            if not chunk:
                raise PrinterError("Printer closed the connection")
            self._buffer += chunk
        frame, self._buffer = FRAME_END.split(self._buffer, maxsplit=1)
        return frame.replace(STX, b"").decode("ascii", errors="ignore").strip()

    def drain(self):
        """Throw away any replies waiting in the buffer (e.g. acks to =ST1)."""
        self._buffer = b""
        self.sock.settimeout(0.0)
        try:
            while self.sock.recv(4096):
                pass
        except (BlockingIOError, socket.timeout):
            pass
        finally:
            self.sock.settimeout(REPLY_TIMEOUT)

    def query(self, payload: str, reply_prefix: str):
        """Send a query and return the first reply that starts with reply_prefix.
        Other replies (acks, status messages) are skipped."""
        self.send(payload)
        deadline = time.monotonic() + REPLY_TIMEOUT
        while True:
            frame = self._read_frame(deadline)
            if frame is None:
                return None
            if frame.startswith(reply_prefix):
                return frame

    def get_print_count(self):
        reply = self.query(CMD_GET_COUNTER, REPLY_COUNTER)
        if reply is None:
            return None
        match = re.search(r"-?\d+", reply[len(REPLY_COUNTER):])
        return int(match.group()) if match else None

    def set_field_text(self, text: str):
        self.send(f"{CMD_SET_FIELD}{text}")


# ==========================================
# 3. DATA + LOG
# ==========================================
def load_serials(path: str):
    """Load the first column as text - keeps leading zeros and avoids '1001.0'."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, header=None, dtype=str)
    else:
        df = pd.read_csv(path, header=None, dtype=str)
    serials = [s.strip() for s in df.iloc[:, 0].dropna() if s.strip()]

    s = pd.Series(serials, dtype=str)
    dupes = sorted(set(s[s.duplicated()]))
    if dupes:
        raise ValueError(f"Dataset contains duplicate serials, e.g. {dupes[:5]}")
    return serials


def load_already_printed(log_path: str):
    if not os.path.exists(log_path):
        return set()
    with open(log_path, newline="") as f:
        return {row["serial"] for row in csv.DictReader(f)}


def append_log(log_path: str, index: int, serial: str, counter: int):
    new_file = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp", "index", "serial", "printer_counter"])
        writer.writerow([dt.datetime.now().isoformat(timespec="seconds"), index, serial, counter])
        f.flush()
        os.fsync(f.fileno())


# ==========================================
# 4. MAIN LOOP
# ==========================================
def read_counter_or_fail(printer: LeibingerPrinter) -> int:
    for _ in range(MAX_QUERY_FAILURES):
        count = printer.get_print_count()
        if count is not None:
            return count
        time.sleep(0.2)
    raise PrinterError(
        f"No valid '{REPLY_COUNTER}' reply to '{CMD_GET_COUNTER}' after "
        f"{MAX_QUERY_FAILURES} tries. Run with --test-connection to see what the printer returns."
    )


def wait_for_print(printer: LeibingerPrinter, baseline: int) -> int:
    """Block until the sensor triggers one print. Returns the new counter value."""
    failures = 0
    while True:
        count = printer.get_print_count()
        if count is None:
            failures += 1
            if failures >= MAX_QUERY_FAILURES:
                raise PrinterError("Lost counter replies while waiting for a product")
        else:
            failures = 0
            if count < baseline:
                raise PrinterError(
                    f"Print counter went backwards ({baseline} -> {count}). "
                    "Was it reset on the printer? Stopping to avoid mis-tracking serials."
                )
            if count > baseline:
                return count
        time.sleep(POLL_INTERVAL)


def run(args):
    print("Leibinger JET2neo - Sensor-Triggered Serial Printing\n")

    print(f"Loading data from '{args.file}'...")
    serials = load_serials(args.file)
    done = load_already_printed(args.log)
    todo = [(i, s) for i, s in enumerate(serials, start=1) if s not in done]
    print(f"Found {len(serials)} records, {len(done)} already printed, {len(todo)} to go.\n")
    if not todo:
        print("[SUCCESS] Nothing left to print.")
        return

    printer = LeibingerPrinter(args.ip, args.port)
    print(f"Connecting to JET2neo at {args.ip}:{args.port}...")
    printer.connect()
    print("Connected.\n")

    try:
        for index, serial in todo:
            # Baseline BEFORE loading: a trigger that happens between here and the
            # load would print old text - that shows up as a counter jump below.
            baseline = read_counter_or_fail(printer)

            printer.set_field_text(serial)
            time.sleep(0.05)   # let the printer apply the new text
            printer.drain()    # discard the =ST1 ack so it can't be mistaken for a reply

            print(f"[{index}/{len(serials)}] Loaded '{serial}' - waiting for sensor trigger...")
            new_count = wait_for_print(printer, baseline)
            printed = new_count - baseline

            append_log(args.log, index, serial, new_count)
            print(f"    [PRINTED] counter {baseline} -> {new_count}")

            if printed > 1:
                raise PrinterError(
                    f"Counter jumped by {printed} while '{serial}' was loaded - "
                    f"{printed - 1} product(s) may carry a duplicate/old code. "
                    "Pull those products and check the line before restarting."
                )

        print("\n[SUCCESS] All records printed.")
    finally:
        printer.close()


def test_connection(args):
    """Send ?PC and dump every raw byte the printer returns, so the reply format can be checked."""
    printer = LeibingerPrinter(args.ip, args.port)
    printer.connect()
    try:
        def dump(seconds):
            data = b""
            end = time.monotonic() + seconds
            while (remaining := end - time.monotonic()) > 0:
                printer.sock.settimeout(remaining)
                try:
                    chunk = printer.sock.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    print("  (printer closed the connection)")
                    break
                data += chunk
            return data

        print("Connected. Listening 2s for anything the printer sends unprompted...")
        unprompted = dump(2.0)
        print(f"  raw: {unprompted!r}" if unprompted else "  (nothing)")

        packet = LeibingerPrinter._frame(CMD_GET_COUNTER)
        print(f"\nSending {packet!r} and listening 3s...")
        printer.sock.sendall(packet)
        reply = dump(3.0)
        if reply:
            print(f"  raw: {reply!r}")
            print(f"  hex: {reply.hex(' ')}")
        else:
            print("  (no reply at all)")
    finally:
        printer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=INPUT_FILE)
    parser.add_argument("--ip", default=PRINTER_IP)
    parser.add_argument("--port", type=int, default=PRINTER_PORT)
    parser.add_argument("--log", default=PRINT_LOG)
    parser.add_argument("--test-connection", action="store_true",
                        help="Query the print counter once and show the raw reply")
    args = parser.parse_args()

    try:
        test_connection(args) if args.test_connection else run(args)
    except KeyboardInterrupt:
        print("\n[STOPPED] by operator. Re-run to resume from the log.")
    except (PrinterError, ValueError, OSError) as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
