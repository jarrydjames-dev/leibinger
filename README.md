# Avis serial printing – Leibinger JET2neo

Prints one unique serial per product from an Excel/CSV file. The printer's photo-eye
triggers each print. Serials are queued in the printer's mailing buffer, so the printer
never prints a serial twice.

## 1. Build the program (once, on a PC that has Python)

1. Put `leibinger_serial_printer.py`, `printer_settings.txt` and `build_exe.bat` in one folder.
2. Double-click `build_exe.bat` (takes 1–3 minutes).
3. It creates the folder `AvisSerialPrinter_Laptop` containing:
   - `AvisSerialPrinter.exe`
   - `printer_settings.txt`

## 2. Set up the operator laptop (no Python needed)

1. Copy the `AvisSerialPrinter_Laptop` folder to the laptop, e.g. to the Desktop.
2. Put the serial files (`.xlsx` or `.csv`) in the same folder:
   - serials in **column A of the first sheet**, starting at **A1**
   - **no header row**
3. Network: the laptop must be on the printer's network (printer IP `192.168.1.100`).
   - Check: open Command Prompt and run `ping 192.168.1.100`. You should get replies.
   - Direct cable from laptop to printer, with no network switch: set the laptop's Ethernet
     adapter to a fixed IP, e.g. `192.168.1.50`, subnet `255.255.255.0`.
   - Different printer IP: edit `printer_settings.txt` (`ip=...`, `port=3000`).
4. Power settings: set the laptop to **never sleep while plugged in**, so a long batch is not
   interrupted.
5. First time only: double-click `AvisSerialPrinter.exe` and choose **T** (test connection).
   You should see the printer version and `OK - this port works`.

## 3. Printing a batch (operator)

1. On the printer: job loaded, jet running, printing **stopped** ("Ready for print start").
2. Double-click `AvisSerialPrinter.exe`.
3. Type the number of the serial file and press ENTER.
4. Check the line `Printing records 1..N` – N must be the number of serials in the file.
5. Press ENTER to start. Each product that passes the sensor gets the next serial.
6. After the last serial, the printer is stopped automatically and the window shows `[SUCCESS]`.

The log `<file name>_log.csv` records every printed serial with time and date. Keep it with
the batch records.

- **Printing stops partway (error or Stop pressed):** acknowledge the message on the printer,
  check the last product, then press ENTER. The program continues from the first unprinted serial.
- **Window closed / laptop restarted:** start the program again and choose the same file.
  It continues where it stopped.
- **Print a finished batch again:** choose the same file, answer `y`, then type `YES`.
  Remove the first-run products with those serials first. The old log is kept as
  `<file name>_log_before_reprint_<date>.csv`.

## Command line (for technicians)

```
AvisSerialPrinter.exe --test-connection
AvisSerialPrinter.exe --file Data.xlsx --log Batch001_log.csv
AvisSerialPrinter.exe --file Data.xlsx --log Batch001_log.csv --reprint --from 3 --to 4
```

The same options work with `python leibinger_serial_printer.py ...`.

## Printer settings required

- Extra / Interface settings / Communication interface: Ethernet, protocol JET3, port 3000
- Extra / Database setting / "Activate database": OFF
- Job: text object with an Extern Text field in **mailing mode, field number 1**, length ≥ serial length
- Job PrintGo source: External (photo-eye)
