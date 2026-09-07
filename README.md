# uptime

A small local tool that monitors your internet connection and reports on outages: how many, when, and for how long.

No dependencies beyond Python 3's standard library.

## Usage

### Monitor (run this in a terminal, leave it running)

```bash
python3 uptime.py monitor
```

Checks connectivity every 10 seconds (ping to `1.1.1.1`/`8.8.8.8`). Prints a line whenever the connection drops or comes back:

```
🔴 Connection lost at 14:02:11
🟢 Connection restored at 14:02:41 (duration: 30s)
```

Press `Ctrl+C` to stop. Restarting after a crash mid-outage correctly resumes tracking that outage instead of losing it.

Add `--verbose` to also log every individual check (not just drops/recoveries) to `data/checks.log` — useful if an outage doesn't get detected and you need to see what each check actually returned.

### Report

```bash
python3 uptime.py report                    # last 7 days
python3 uptime.py report --days 30          # last 30 days
python3 uptime.py report --date 2026-09-06  # a specific day
```

```
Saturday 2026-09-05: 2 outage(s), 5 min 40s total
  - 10:15 -> 10:19 (4 min 30s)
  - 14:02 -> 14:03 (1 min 10s)
Sunday 2026-09-06: no outages
```

### Dashboard

```bash
python3 uptime.py dashboard                 # last 30 days, opens in your browser
python3 uptime.py dashboard --days 7 --no-open --output ~/Desktop/report.html
```

Generates a self-contained HTML page (no external dependencies) with summary stats, a daily downtime chart, and a full outage table.

## How it works

Data is stored in `data/events.jsonl`, an append-only log of connection state changes (not every individual check). `report` and `dashboard` read this file; only `monitor` writes to it.

**Don't edit or delete `data/events.jsonl` while `monitor` is running** — it's your real outage history and isn't backed up.
