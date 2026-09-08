#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "events.jsonl"
CHECKS_LOG_FILE = Path(__file__).parent / "data" / "checks.log"

CHECK_INTERVAL = 10  # seconds between checks
FAILURE_THRESHOLD = 3  # consecutive failures before declaring "down"
RECOVERY_THRESHOLD = 2  # consecutive successes before declaring "up"
HEARTBEAT_INTERVAL = 600  # seconds between "still running" heartbeats

PING_HOSTS = ["1.1.1.1", "8.8.8.8"]

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def format_day_label(day) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.strftime('%d.%m.%Y')}"


def ping(host: str) -> bool:
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-t", "2", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_connectivity() -> tuple[bool, str]:
    for host in PING_HOSTS:
        if ping(host):
            return True, f"ping:{host}"
    return False, "all_failed"


def log_check(timestamp: datetime, connected: bool, detail: str) -> None:
    CHECKS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CHECKS_LOG_FILE.open("a") as f:
        f.write(json.dumps({"timestamp": timestamp.isoformat(), "connected": connected, "detail": detail}) + "\n")


def append_event(event: str, timestamp: datetime) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("a") as f:
        f.write(json.dumps({"event": event, "timestamp": timestamp.isoformat()}) + "\n")


def read_events() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    events = []
    with DATA_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def format_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}min"
    if minutes:
        return f"{minutes} min {secs:02d}s" if secs else f"{minutes} min"
    return f"{secs}s"


def cmd_monitor(args: argparse.Namespace) -> None:
    events = read_events()
    is_down = bool(events) and events[-1]["event"] == "down"
    down_start = datetime.fromisoformat(events[-1]["timestamp"]) if is_down else None

    if is_down:
        print(f"Resuming after interruption: outage in progress since {down_start.strftime('%H:%M:%S')}.")

    consecutive_fail = 0
    consecutive_success = 0
    pending_down_start = None
    pending_up_start = None
    # Backdated so the first heartbeat fires after one CHECK_INTERVAL instead
    # of making the user wait a full HEARTBEAT_INTERVAL for proof of life.
    last_heartbeat = datetime.now() - timedelta(seconds=HEARTBEAT_INTERVAL - CHECK_INTERVAL)

    print(f"Monitoring connection (checking every {CHECK_INTERVAL}s). Press Ctrl+C to stop.")
    if args.verbose:
        print(f"Verbose mode: logging every check to {CHECKS_LOG_FILE}")

    try:
        while True:
            now = datetime.now()
            connected, detail = check_connectivity()
            if args.verbose:
                log_check(now, connected, detail)

            if (now - last_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
                print(f"💓 Heartbeat at {now.strftime('%H:%M:%S')}")
                last_heartbeat = now

            if connected:
                if not is_down:
                    # Leaky bucket: a single stray success during a flapping
                    # outage (box retrying its uplink) shouldn't erase all
                    # progress toward declaring "down" — only decay it by one.
                    consecutive_fail = max(0, consecutive_fail - 1)
                    consecutive_success = 0
                    pending_up_start = None
                else:
                    consecutive_fail = 0
                    if consecutive_success == 0:
                        pending_up_start = now
                    consecutive_success += 1
                    if consecutive_success >= RECOVERY_THRESHOLD:
                        up_time = pending_up_start or now
                        append_event("up", up_time)
                        duration = (up_time - down_start).total_seconds()
                        print(f"🟢 Connection restored at {up_time.strftime('%H:%M:%S')} (duration: {format_duration(duration)})")
                        is_down = False
                        consecutive_success = 0
                        pending_up_start = None
            else:
                consecutive_success = 0
                if is_down:
                    pass
                else:
                    if consecutive_fail == 0:
                        pending_down_start = now
                    consecutive_fail += 1
                    if consecutive_fail >= FAILURE_THRESHOLD:
                        down_start = pending_down_start or now
                        append_event("down", down_start)
                        print(f"🔴 Connection lost at {down_start.strftime('%H:%M:%S')}")
                        is_down = True
                        consecutive_fail = 0
                        pending_down_start = None

            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def build_outages(events: list[dict]) -> list[dict]:
    outages = []
    current_start = None
    for event in events:
        ts = datetime.fromisoformat(event["timestamp"])
        if event["event"] == "down":
            current_start = ts
        elif event["event"] == "up" and current_start is not None:
            outages.append({"start": current_start, "end": ts})
            current_start = None
    if current_start is not None:
        outages.append({"start": current_start, "end": datetime.now(), "ongoing": True})
    return outages


def cmd_report(args: argparse.Namespace) -> None:
    events = read_events()
    outages = build_outages(events)

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        dates = [target_date]
    else:
        today = datetime.now().date()
        dates = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    by_day = defaultdict(list)
    for outage in outages:
        by_day[outage["start"].date()].append(outage)

    for day in dates:
        day_outages = sorted(by_day.get(day, []), key=lambda o: o["start"])
        label = format_day_label(day)
        if not day_outages:
            print(f"{label}: no outages")
            continue
        total_seconds = sum((o["end"] - o["start"]).total_seconds() for o in day_outages)
        print(f"{label}: {len(day_outages)} outage(s), {format_duration(total_seconds)} total")
        for o in day_outages:
            start_str = o["start"].strftime("%H:%M")
            end_str = o["end"].strftime("%H:%M") + (" (ongoing)" if o.get("ongoing") else "")
            duration = format_duration((o["end"] - o["start"]).total_seconds())
            print(f"  - {start_str} -> {end_str} ({duration})")


def compute_dashboard_data(events: list[dict], days: int) -> dict:
    outages = build_outages(events)
    today = datetime.now().date()
    start_date = today - timedelta(days=days - 1)

    filtered = [o for o in outages if o["start"].date() >= start_date]

    daily = {start_date + timedelta(days=i): {"total_seconds": 0.0, "count": 0} for i in range(days)}
    for o in filtered:
        day = o["start"].date()
        duration = (o["end"] - o["start"]).total_seconds()
        daily[day]["total_seconds"] += duration
        daily[day]["count"] += 1

    total_outages = len(filtered)
    total_downtime = sum((o["end"] - o["start"]).total_seconds() for o in filtered)
    avg_duration = total_downtime / total_outages if total_outages else 0.0
    avg_outages_per_day = total_outages / days

    return {
        "start_date": start_date,
        "end_date": today,
        "days": days,
        "daily": daily,
        "total_outages": total_outages,
        "total_downtime_seconds": total_downtime,
        "avg_duration_seconds": avg_duration,
        "avg_outages_per_day": avg_outages_per_day,
        "outages": sorted(filtered, key=lambda o: o["start"], reverse=True),
    }


def render_dashboard_html(data: dict) -> str:
    days_list = sorted(data["daily"].keys())
    max_seconds = max((d["total_seconds"] for d in data["daily"].values()), default=0) or 1

    chart_width = 760
    chart_height = 180
    bar_gap = 3
    bar_width = max(2, (chart_width / len(days_list)) - bar_gap)
    label_step = max(1, len(days_list) // 8)

    bars = []
    for i, day in enumerate(days_list):
        info = data["daily"][day]
        x = i * (chart_width / len(days_list))
        height = (info["total_seconds"] / max_seconds) * (chart_height - 4) if info["total_seconds"] else 0
        y = chart_height - height
        title = f"{day.strftime('%a %d.%m.%Y')}: {info['count']} outage(s), {format_duration(info['total_seconds'])}"
        bars.append(
            f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{max(height, 1):.1f}" rx="2"><title>{title}</title></rect>'
        )
        if info["total_seconds"] > 0:
            minutes = max(1, round(info["total_seconds"] / 60))
            bars.append(
                f'<text class="downtime-label" x="{x + bar_width / 2:.1f}" y="{y - 6:.1f}" '
                f'text-anchor="middle">{minutes}m</text>'
            )
        if i % label_step == 0 or i == len(days_list) - 1:
            label_x = x + bar_width / 2
            bars.append(
                f'<text class="axis-label" x="{label_x:.1f}" y="{chart_height + 16}" '
                f'text-anchor="middle">{day.strftime("%d.%m")}</text>'
            )

    bars_svg = "\n".join(bars)

    # Second chart: outage count per day, with a flat average line.
    counts = [data["daily"][day]["count"] for day in days_list]
    max_count = max(counts, default=0) or 1
    average = data["avg_outages_per_day"]

    count_bars = []
    for i, day in enumerate(days_list):
        x = i * (chart_width / len(days_list))
        center_x = x + bar_width / 2
        count = counts[i]
        height = (count / max_count) * (chart_height - 4) if count else 0
        dot_y = chart_height - height
        title = f"{day.strftime('%a %d.%m.%Y')}: {count} outage(s)"
        count_bars.append(
            f'<line class="lollipop-stem" x1="{center_x:.1f}" y1="{chart_height}" '
            f'x2="{center_x:.1f}" y2="{dot_y:.1f}"></line>'
            f'<circle class="lollipop-dot" cx="{center_x:.1f}" cy="{dot_y:.1f}" r="5"><title>{title}</title></circle>'
        )
        if count > 0:
            count_bars.append(
                f'<text class="count-label" x="{center_x:.1f}" y="{dot_y - 10:.1f}" '
                f'text-anchor="middle">{count}</text>'
            )
        if i % label_step == 0 or i == len(days_list) - 1:
            count_bars.append(
                f'<text class="axis-label" x="{center_x:.1f}" y="{chart_height + 16}" '
                f'text-anchor="middle">{day.strftime("%d.%m")}</text>'
            )

    count_bars_svg = "\n".join(count_bars)
    average_y = chart_height - (average / max_count) * (chart_height - 4)
    average_line_svg = f'<line class="average-line" x1="0" y1="{average_y:.1f}" x2="{chart_width}" y2="{average_y:.1f}"></line>'

    if data["outages"]:
        rows = []
        for o in data["outages"]:
            duration = format_duration((o["end"] - o["start"]).total_seconds())
            end_label = o["end"].strftime("%H:%M") + (" (ongoing)" if o.get("ongoing") else "")
            rows.append(
                "<tr>"
                f'<td>{o["start"].strftime("%d.%m.%Y")}</td>'
                f'<td class="num">{o["start"].strftime("%H:%M")}</td>'
                f'<td class="num">{end_label}</td>'
                f'<td class="num">{duration}</td>'
                "</tr>"
            )
        table_body = "\n".join(rows)
    else:
        table_body = '<tr><td colspan="4" class="empty">No outages in this period</td></tr>'

    period_label = f'{data["start_date"].strftime("%d.%m.%Y")} - {data["end_date"].strftime("%d.%m.%Y")} ({data["days"]} days)'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Internet Uptime Dashboard</title>
<style>
  .viz-root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page-plane: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --baseline: #c3c2b7;
    --series-1: #2a78d6;
    --status-critical: #d03b3b;
    --border: rgba(11,11,11,0.10);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page-plane);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    color: var(--text-primary);
  }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 32px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--text-secondary); margin: 0 0 28px; font-size: 14px; }}
  .tiles {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 28px; }}
  .tile {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px;
  }}
  .tile .label {{ font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }}
  .tile .value {{ font-size: 24px; font-weight: 600; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
    padding: 20px; margin-bottom: 28px;
  }}
  .card h2 {{ font-size: 14px; margin: 0 0 16px; color: var(--text-secondary); }}
  svg {{ width: 100%; height: auto; overflow: visible; }}
  .bar {{ fill: var(--status-critical); }}
  .bar:hover {{ opacity: 0.75; }}
  .downtime-label {{ fill: var(--status-critical); font-size: 10px; font-variant-numeric: tabular-nums; }}
  .axis-label {{ fill: var(--text-muted); font-size: 10px; }}
  .baseline {{ stroke: var(--baseline); stroke-width: 1; }}
  .average-line {{ stroke: var(--series-1); stroke-width: 2; stroke-dasharray: 4 3; }}
  .lollipop-stem {{ stroke: var(--status-critical); stroke-width: 2; }}
  .lollipop-dot {{ fill: var(--status-critical); }}
  .count-label {{ fill: var(--status-critical); font-size: 10px; font-variant-numeric: tabular-nums; }}
  .legend {{ display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px; color: var(--text-secondary); }}
  .legend-item {{ display: flex; align-items: center; gap: 6px; }}
  .legend-swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--gridline); }}
  th {{ color: var(--text-muted); font-weight: 500; font-size: 12px; }}
  td.num {{ font-variant-numeric: tabular-nums; color: var(--text-secondary); }}
  td.empty {{ color: var(--text-muted); text-align: center; padding: 24px; }}
</style>
</head>
<body>
<div class="viz-root wrap">
  <h1>Internet Uptime Dashboard</h1>
  <p class="subtitle">{period_label}</p>

  <div class="tiles">
    <div class="tile"><div class="label">Outages</div><div class="value">{data["total_outages"]}</div></div>
    <div class="tile"><div class="label">Total downtime</div><div class="value">{format_duration(data["total_downtime_seconds"])}</div></div>
    <div class="tile"><div class="label">Avg. outage length</div><div class="value">{format_duration(data["avg_duration_seconds"])}</div></div>
    <div class="tile"><div class="label">Avg. outages/day</div><div class="value">{data["avg_outages_per_day"]:.2f}</div></div>
  </div>

  <div class="card">
    <h2>Downtime per day</h2>
    <svg viewBox="0 0 {chart_width} {chart_height + 24}" preserveAspectRatio="none">
      <line class="baseline" x1="0" y1="{chart_height}" x2="{chart_width}" y2="{chart_height}"></line>
      {bars_svg}
    </svg>
  </div>

  <div class="card">
    <h2>Outages per day</h2>
    <div class="legend">
      <span class="legend-item"><span class="legend-swatch" style="background: var(--status-critical)"></span>Outages/day</span>
      <span class="legend-item"><span class="legend-swatch" style="background: var(--series-1)"></span>Average</span>
    </div>
    <svg viewBox="0 0 {chart_width} {chart_height + 24}" preserveAspectRatio="none">
      <line class="baseline" x1="0" y1="{chart_height}" x2="{chart_width}" y2="{chart_height}"></line>
      {count_bars_svg}
      {average_line_svg}
    </svg>
  </div>

  <div class="card">
    <h2>Outages ({data["total_outages"]})</h2>
    <table>
      <thead><tr><th>Date</th><th>Start</th><th>End</th><th>Duration</th></tr></thead>
      <tbody>
        {table_body}
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


def cmd_dashboard(args: argparse.Namespace) -> None:
    events = read_events()
    data = compute_dashboard_data(events, args.days)
    html = render_dashboard_html(data)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html)

    print(f"Dashboard written to {output_path.resolve()}")

    if not args.no_open:
        webbrowser.open(f"file://{output_path.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Internet outage tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    monitor_parser = subparsers.add_parser("monitor", help="Continuously monitor the connection")
    monitor_parser.add_argument("--verbose", action="store_true", help="Log every check (not just transitions) to data/checks.log")

    report_parser = subparsers.add_parser("report", help="Show an outage report")
    report_parser.add_argument("--date", help="Specific day in YYYY-MM-DD format")
    report_parser.add_argument("--days", type=int, default=7, help="Number of days to show (default: 7)")

    dashboard_parser = subparsers.add_parser("dashboard", help="Generate an HTML dashboard")
    dashboard_parser.add_argument("--days", type=int, default=30, help="Number of days to cover (default: 30)")
    dashboard_parser.add_argument("--output", default=str(DATA_FILE.parent / "dashboard.html"), help="Output HTML file path")
    dashboard_parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard in a browser")

    args = parser.parse_args()

    if args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)


if __name__ == "__main__":
    main()
