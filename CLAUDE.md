# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python CLI (`uptime.py`, stdlib only, no dependencies) that monitors internet connectivity and reports on outages. Three subcommands: `monitor` (runs continuously, logs state transitions), `report` (CLI text summary), `dashboard` (generates a static HTML page).

## Commands

```bash
python3 uptime.py monitor                        # run continuously, Ctrl+C to stop
python3 uptime.py monitor --verbose               # also log every check (not just transitions) to data/checks.log
python3 uptime.py report --days 7                # text report, last N days (default 7)
python3 uptime.py report --date 2026-09-06       # text report for a specific day
python3 uptime.py dashboard --days 30 --no-open  # generate data/dashboard.html
python3 -m py_compile uptime.py                  # syntax check (no test suite exists)
```

There is no build step, linter, or test suite configured for this project.

## Architecture

- **Data model**: only state *transitions* are persisted, not every check. `data/events.jsonl` is an append-only log of `{"event": "down"|"up", "timestamp": ...}` lines. An outage is reconstructed by pairing a `down` with the next `up` (`build_outages()`); an unpaired trailing `down` means an outage still in progress.
- **Connectivity check** (`check_connectivity`): ping `1.1.1.1`/`8.8.8.8` only, returns `(connected, detail)`. Uses macOS/BSD `ping` flags (`-c`, `-t`) — not portable to Linux as-is. There used to be an HTTP GET fallback (`google.com`/`cloudflare.com`) for networks that block ICMP; it was removed after real-world evidence (via `--verbose`/`checks.log`) showed `urllib.request.urlopen(url, timeout=3)` can block for ~74s during a real outage — the `timeout` parameter doesn't bound DNS resolution (`getaddrinfo`), so during an outage a single check could take far longer than `CHECK_INTERVAL`, which delayed or entirely prevented `FAILURE_THRESHOLD` from being reached before the outage self-resolved. If a network-blocks-ICMP fallback is ever reintroduced, it must run under a hard wall-clock timeout (e.g. a thread with `future.result(timeout=...)`), not rely on `urlopen`'s `timeout` alone.
- **Debounce state machine** (in `cmd_monitor`): raw check results are noisy, so `FAILURE_THRESHOLD`/`RECOVERY_THRESHOLD` consecutive-result counters gate the actual `down`/`up` events that get logged, and the logged timestamp is backdated to the *first* failing/succeeding check in the streak (not the moment the threshold was crossed) so the recorded duration matches reality. The failure counter decays by 1 on a lone success instead of hard-resetting to 0 (leaky bucket), so a single stray successful check during a flapping outage (e.g. a router mid-reconnect) doesn't erase all progress toward declaring "down".
- **`--verbose` check log**: `monitor --verbose` appends every raw check result (not just transitions) to `data/checks.log` as JSONL (`{timestamp, connected, detail}`). Use it to diagnose detection issues with hard data instead of guessing — it's what surfaced the DNS-hang bug above.
- **Resume-on-restart**: `cmd_monitor` inspects the last line of `events.jsonl` on startup — if it's an unpaired `down`, it resumes in the "down" state instead of assuming connectivity.
- **`report` and `dashboard` are pure readers**: both call `read_events()` + `build_outages()` and never write to the log. Only `cmd_monitor` (via `append_event`) writes.
- **Dashboard HTML** (`render_dashboard_html`): self-contained, no external CSS/JS/CDN dependencies (deliberate, given the subject matter — a network monitoring tool shouldn't need network access to render its own report). Chart is hand-built SVG, not a charting library. Theme is fixed light (previously had a `prefers-color-scheme: dark` block; removed after a contrast bug made numbers unreadable).

## Working conventions specific to this repo

- All CLI output and code comments are in English (was translated from an initial French version at the user's request).
- `data/events.jsonl` may be the user's live, real monitoring data (written by a long-running `monitor` process). **Never write test/sample events into `data/events.jsonl` directly** — a real `monitor` process may be running in the background and appending to it, and overwriting or deleting this file destroys unrecoverable outage history. Use a separate path (e.g. pass a temp file, or copy the read/report logic against a scratch file) for any testing.
- **Never start a second `monitor` process for testing while a real one may already be running** — check `ps aux | grep uptime.py` first. Two concurrent `monitor` processes each keep independent in-memory state and both append to the same `data/events.jsonl`, producing duplicate/out-of-order `down`/`up` lines that `build_outages()` doesn't handle gracefully (it pairs strictly by file order, so duplicates silently corrupt the reconstructed outage list). If a background test process is started by mistake, verify with `ps` that `kill` actually terminated it — a background shell job here can survive a plain `kill -INT`.
