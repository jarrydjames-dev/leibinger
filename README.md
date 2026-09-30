# UMS Serialine

Prints one unique serial per product from an Excel/CSV file on a Leibinger JET2neo.
The line's photo-eye triggers each print. Serials are queued in the printer's mailing
buffer, so the printer never prints a serial twice. Every printed serial is logged.

Full instructions are in the printed manuals:
- **UMS Serialine – User Manual** (for the client)
- **UMS Serialine – Technician Installation Manual** (for UMS technicians)

## Files

| File | Purpose |
| --- | --- |
| `serialine.py` | The program |
| `printer_settings.txt` | Printer IP and port (`ip=...`, `port=3000`) |
| `build_exe.bat` | Builds `Serialine.exe` into `Serialine_Package\` (run on a PC with Python) |
| `requirements.txt` | Python packages (`pip install -r requirements.txt`) |

## Quick start

1. Double-click `build_exe.bat`. It creates `Serialine_Package\Serialine.exe` and `printer_settings.txt`.
2. Copy `Serialine_Package` to the client PC and put the serial files in it.
   Format: column A of the first sheet, from A1, no header row.
3. Double-click `Serialine.exe`, then type **T** to test the connection.
4. Double-click `Serialine.exe`, choose the file, check `Printing records 1..N`, press ENTER.

## Command line

```
Serialine.exe --test-connection
Serialine.exe --file Batch.xlsx                     (log: Batch_log.csv)
Serialine.exe --file Batch.xlsx --reprint
Serialine.exe --file Batch.xlsx --reprint --from 120 --to 135
```

The same options work with `python serialine.py ...`.

## Printer settings required

- Extra / Interface settings / Communication interface: Ethernet, protocol JET3, port 3000
- Extra / Database setting / "Activate database": OFF
- Job: text object with an Extern Text field in **mailing mode, field number 1**, length ≥ serial length
- Job PrintGo source: External (photo-eye)
