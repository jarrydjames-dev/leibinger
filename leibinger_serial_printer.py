"""
Leibinger JET2neo - sensor-triggered unique serial printing (Avis Labs)

Uses the printer's MAILING mode (Leibinger Interface Protocol v1.9.15, Annex B):

  * Serials are sent into the printer's mailing FIFO (up to 256 records) with
    ^0=MR<record no><TAB><serial>.
  * On every PrintGo signal (the photo-eye) the printer prints the NEXT record
    from the FIFO. No sensor signal = no print. The printer itself blocks double
    prints and stops with an error if a record is missing or out of order.
  * This script keeps the FIFO topped up, logs every serial the printer reports
    as printed (^0?SM "last printed record number"), and makes the printer stop
    automatically after the last serial (^0=CM).

Printer setup:
  * Extra / Interface settings / Communication interface: Ethernet, protocol JET3, port 3000
  * Extra / Database setting / "Activate database" must be OFF
  * The print job needs a text object with an EXTERN TEXT field in mail mode,
    field number 1, with enough placeholder characters for the serial.
  * Job PrintGo source: External (the photo-eye), not internal.

Usage
-----
    python leibinger_serial_printer.py --test-connection   # check comms + job
    python leibinger_serial_printer.py                     # print Data.csv
    python leibinger_serial_printer.py --file Data.xlsx --ip 192.168.1.100
    python leibinger_serial_printer.py --file Data.xlsx --log B1_log.csv --reprint              # whole batch again
    python leibinger_serial_printer.py --file Data.xlsx --log B1_log.csv --reprint --from 3 --to 4  # records 3-4 again
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
PRINTER_PORT = 3000            # Leibinger interface port (manual default: 3000)
INPUT_FILE = "Data.csv"        # .csv, .xlsx or .xls - serials in the first column
PRINT_LOG = "print_log.csv"    # Every printed serial is appended here (audit + resume)

POLL_INTERVAL = 0.2            # Seconds between status polls
REPLY_TIMEOUT = 2.0            # Seconds to wait for a reply to a query
REFILL_BLOCK = 50              # Top up the FIFO once this many slots are free

# Machine states from ^0=RS parameter 2
STATE_NAMES = {1: "Standby", 2: "Initialising", 3: "Interval/Service",
               4: "Ready for action (jet not ready to print)",
               5: "Ready for print start", 6: "Printing"}
STATE_READY, STATE_PRINTING = 5, 6


class PrinterError(Exception):
    pass


# ==========================================
# 2. PRINTER CONNECTION (^0 ... <CR> frames)
# ==========================================
class LeibingerPrinter:
    def __init__(self, ip: str, port: int):
        self.ip, self.port = ip, port
        self.sock = None
        self._buffer = b""

    def connect(self):
        self.sock = socket.create_connection((self.ip, self.port), timeout=5.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, payload: str):
        self.sock.sendall(b"^0" + payload.encode("latin-1") + b"\r")

    def read_line(self, deadline: float):
        """Next reply line without the ^0 prefix and CR/LF, or None on timeout."""
        while True:
            m = re.search(rb"[\r\n]", self._buffer)
            if m:
                line, self._buffer = self._buffer[:m.start()], self._buffer[m.end():]
                line = line.strip()
                if not line:
                    continue          # empty line / LF after CR
                if line.startswith(b"^0"):
                    line = line[2:]
                return line.decode("latin-1")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise PrinterError("Printer closed the connection")
            self._buffer += chunk

    def query(self, code: str, timeout: float = REPLY_TIMEOUT):
        """Send ?XX and return the parameters of the =XX reply (other lines are skipped)."""
        self.send("?" + code)
        deadline = time.monotonic() + timeout
        while (line := self.read_line(deadline)) is not None:
            if line.startswith("=" + code):
                return line[len(code) + 1:]
        raise PrinterError(f"No reply to ?{code} - check IP/port and that the interface protocol is JET3")

    def query_numbers(self, code: str):
        return [int(n) for n in re.findall(r"-?\d+", self.query(code))]

    def status(self):
        p = self.query_numbers("RS")   # nozzle, machine state, error, head cover, speed, job change
        return {"nozzle": p[0], "state": p[1], "error": p[2] if len(p) > 2 else 0}

    def mail_status(self):
        p = self.query_numbers("SM")   # fifo depth, entries, last printed, stop at, finished, printgos
        return {"depth": p[0], "entries": p[1], "last_printed": p[2], "stop_at": p[3]}

    def total_print_counter(self):
        p = self.query_numbers("CC")   # product counter, stop after X, total print counter
        return p[2] if len(p) > 2 else p[0]

    def send_mail_record(self, number: int, text: str):
        self.send(f"=MR{number}\t{escape_field(text)}")


def escape_field(text: str) -> str:
    """^ and \\ must be escaped in data; TAB/CR/LF are not allowed."""
    if re.search(r"[\t\r\n]", text):
        raise ValueError(f"Serial {text!r} contains a TAB or line break")
    return text.replace("\\", "\\\\").replace("^", "\\^")


def error_code(raw: int) -> int:
    """=RS error number: bits 25-31 are flags, the rest is the error code."""
    return raw & 0x1FFFFFF


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
    for serial in serials:
        escape_field(serial)
    return serials


def last_logged_record(log_path: str, serials) -> int:
    """Highest record number already printed according to the log (0 = none)."""
    if not os.path.exists(log_path):
        return 0
    last = 0
    with open(log_path, newline="") as f:
        for row in csv.DictReader(f):
            no = int(row["record"])
            if no > len(serials) or serials[no - 1] != row["serial"]:
                raise ValueError(f"'{log_path}' does not match the data file (record {no}). "
                                 "Use a new --log file for a new dataset.")
            last = max(last, no)
    return last


def append_log(log_path: str, rows):
    new_file = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["timestamp", "record", "serial"])
        now = dt.datetime.now().isoformat(timespec="seconds")
        writer.writerows([now, no, serial] for no, serial in rows)
        f.flush()
        os.fsync(f.fileno())


# ==========================================
# 4. MAIN LOOP
# ==========================================
def wait_for_ready(printer: LeibingerPrinter):
    announced = None
    while True:
        st = printer.status()
        if st["state"] == STATE_READY and st["error"] == 0:
            return
        if st["state"] == STATE_PRINTING:
            msg = "Printer is PRINTING - stop printing on the printer so the queue can be loaded cleanly."
        elif st["error"]:
            msg = f"Printer reports error/message {error_code(st['error'])} - acknowledge it on the printer."
        else:
            msg = f"Printer state: {STATE_NAMES.get(st['state'], st['state'])} - start the jet."
        if msg != announced:
            print(f"    ... {msg}")
            announced = msg
        time.sleep(1.0)


def send_records(printer, serials, next_no, count):
    """Send up to count records starting at next_no. Returns the next record number to send.

    After the last serial a blank end-marker record is added: the printer stops with an
    error if its mailing queue runs empty, so the last serial must never be the last
    record in the queue. The script stops printing as soon as the last serial is printed.
    """
    end = min(len(serials), next_no + count - 1)
    for no in range(next_no, end + 1):
        printer.send_mail_record(no, serials[no - 1])
    if end == len(serials):
        printer.send_mail_record(end + 1, "")
    return end + 1


def prime_and_start(printer, serials, first_no, auto_start):
    """Load the FIFO starting at record first_no, set auto-stop, start printing."""
    wait_for_ready(printer)
    time.sleep(1.0)                           # a print stop clears mailing data - let it settle
    printer.send("!FF")                       # clear any old records
    time.sleep(0.3)

    # Ask the printer to stop after the last record. Not every firmware accepts this
    # (the JET2neo V75.0.11.2 reports 0) - the blank end marker + !ST cover the end anyway.
    printer.send(f"=CM{len(serials)}")
    time.sleep(0.3)
    sm = printer.mail_status()

    next_no = send_records(printer, serials, first_no, sm["depth"] - 1)
    sent = next_no - first_no

    # Check the printer really took the records (FIFO entries = records sent - 1)
    if sent > 1:
        for _ in range(10):
            time.sleep(0.3)
            sm = printer.mail_status()
            if sm["entries"] > 0:
                break
        else:
            raise PrinterError(
                f"Printer did not accept the serials into its mailing queue (?SM reply: {sm}).\n"
                "  Check: 'Activate database' is OFF, the loaded job has the mailing field "
                "(EXTTXT field no. 1), and the job was re-loaded after editing.")
    print(f"\n  {sent} serial(s) queued in the printer (records {first_no}..{next_no - 1}).")
    if not auto_start:
        input("  Press ENTER to start printing (each sensor trigger prints the next serial)...")
    printer.send("!GO")
    printer.send(f"=CM{len(serials)}")        # some firmware only keeps this once printing
    print("  Printing started - waiting for products on the sensor.\n")
    return next_no


def archive_log_for_reprint(args, first, total, serials):
    """Move the existing log aside (kept for the audit trail) so the batch can be printed again."""
    with open(args.log, newline="") as f:
        already = sum(1 for row in csv.DictReader(f) if first <= int(row["record"]) <= total)
    print(f"\n  REPRINT: '{args.log}' shows {already} of records {first}..{total} already printed.")
    print(f"  Reprinting puts serials {serials[first - 1]} .. {serials[total - 1]} on NEW products -")
    print("  make sure the first-run products with these serials are removed/destroyed.")
    if not args.yes and input("  Type YES to reprint: ").strip() != "YES":
        raise ValueError("Reprint cancelled")
    stem, ext = os.path.splitext(args.log)
    archived = f"{stem}_before_reprint_{dt.datetime.now():%Y%m%d_%H%M%S}{ext}"
    os.replace(args.log, archived)
    print(f"  Previous log kept as '{archived}'. Starting a new '{args.log}'.\n")


def run(args):
    print("Leibinger JET2neo - Sensor-Triggered Serial Printing (mailing mode)\n")

    print(f"Loading data from '{args.file}'...")
    serials = load_serials(args.file)
    first = args.from_record or 1
    total = args.to_record or len(serials)
    if not 1 <= first <= total <= len(serials):
        raise ValueError(f"--from/--to must be within 1..{len(serials)} (got {first}..{total})")

    if args.reprint and os.path.exists(args.log):
        archive_log_for_reprint(args, first, total, serials)

    logged = last_logged_record(args.log, serials)
    print(f"Found {len(serials)} records. Printing records {first}..{total} ({total - first + 1}).")
    logged = min(max(logged, first - 1), total)
    serials = serials[:total]          # records after --to are never sent
    print(f"  {logged - first + 1} already printed, {total - logged} to go.")
    if logged >= total:
        print("[SUCCESS] Nothing left to print. To print this batch again, add --reprint")
        return

    printer = LeibingerPrinter(args.ip, args.port)
    print(f"Connecting to JET2neo at {args.ip}:{args.port}...")
    printer.connect()
    print("Connected.")

    try:
        next_no = prime_and_start(printer, serials, logged + 1, args.yes)
        fifo_depth = printer.mail_status()["depth"]
        was_printing = False

        while logged < total:
            time.sleep(POLL_INTERVAL)
            sm = printer.mail_status()
            st = printer.status()

            # Log everything the printer reports as printed since the last poll
            last = min(sm["last_printed"], total)   # total+1 = blank end marker
            if logged < last:
                rows = [(no, serials[no - 1]) for no in range(logged + 1, last + 1)]
                append_log(args.log, rows)
                for no, serial in rows:
                    print(f"  [PRINTED] {no}/{total}: {serial}")
                logged = last
                if logged >= total:
                    printer.send("!ST")   # stop before the next product gets the blank marker
                    break

            if st["state"] == STATE_PRINTING:
                was_printing = True
                queued = (next_no - 1) - logged        # sent but not yet printed
                free = fifo_depth - 1 - queued
                if next_no <= total and free >= REFILL_BLOCK:
                    next_no = send_records(printer, serials, next_no, free)
                continue

            if logged >= total or not was_printing:
                continue   # finished, or !GO not processed yet

            # Printing stopped before the end (operator stop, error, ...). The printer
            # clears its FIFO on stop, so re-queue from the first unprinted record.
            print(f"\n[STOPPED] Printing stopped after record {logged} "
                  f"({STATE_NAMES.get(st['state'], st['state'])}, error {error_code(st['error'])}).")
            next_no = prime_and_start(printer, serials, logged + 1, auto_start=False)
            was_printing = False

        print(f"\n[SUCCESS] Records {first}..{total} printed - printing stopped. Log: {args.log}")
    finally:
        printer.close()


def test_connection(args):
    for port in dict.fromkeys([args.port, 3000, 2201]):
        print(f"--- {args.ip}:{port} ---")
        printer = LeibingerPrinter(args.ip, port)
        try:
            printer.connect()
        except OSError as e:
            print(f"  cannot connect: {e}\n")
            continue
        try:
            print(f"  Version       : {printer.query('VS').split(chr(9))}")
            st = printer.status()
            print(f"  Machine state : {STATE_NAMES.get(st['state'], st['state'])}, "
                  f"error {error_code(st['error'])}")
            sm = printer.mail_status()
            print(f"  Mailing FIFO  : depth {sm['depth']}, entries {sm['entries']}, "
                  f"last printed {sm['last_printed']}")
            print(f"  Print counter : {printer.total_print_counter()}")
            print(f"  Loaded job    : {printer.query('JL').split(chr(9))[0]}")

            printer.send("?JB")
            lines, deadline = [], time.monotonic() + 3.0
            while (line := printer.read_line(deadline)) is not None:
                lines.append(line)
                if "ENDLJSCRIPT" in line:
                    break
            fields = [l for l in lines if "EXTTXT" in l]
            print("  Extern-text fields in the job (3rd value = field no.; must be 1 for mailing):")
            for l in fields or ["(none found - the job has no extern text / mailing field!)"]:
                print(f"    {l}")
            print(f"\n  OK - this port works. Use --port {port}\n")
        except PrinterError as e:
            print(f"  connected, but: {e}\n")
        finally:
            printer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=INPUT_FILE)
    parser.add_argument("--ip", default=PRINTER_IP)
    parser.add_argument("--port", type=int, default=PRINTER_PORT)
    parser.add_argument("--log", default=PRINT_LOG)
    parser.add_argument("--yes", action="store_true", help="Start printing without pressing ENTER")
    parser.add_argument("--reprint", action="store_true",
                        help="Print the batch (or --from/--to range) again; the old log is kept as a copy")
    parser.add_argument("--from", dest="from_record", type=int, help="First record number to print (default 1)")
    parser.add_argument("--to", dest="to_record", type=int, help="Last record number to print (default: last)")
    parser.add_argument("--test-connection", action="store_true",
                        help="Check communication, printer state and the loaded job")
    args = parser.parse_args()

    try:
        test_connection(args) if args.test_connection else run(args)
    except KeyboardInterrupt:
        print("\n[STOPPED] by operator. Stop printing on the printer; re-run to resume from the log.")
    except (PrinterError, ValueError, OSError) as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
