#!/usr/bin/env python3
"""
wake_monitor.py - live classifier for screen wakes on the Galaxy C9 Pro (stock Oreo ROM).
Watches `adb logcat` for display power-on events and the panel-dead uevent and prints one line
per wake: OK, or LOOP with how many panel resets happened. Ctrl+C prints a summary.

  python3 -I wake_monitor.py            # then lock/unlock the phone ~20-30 times, ~10 s apart
  python3 -I wake_monitor.py saved.log  # replay a saved `adb logcat -v time` dump instead
"""
import re, subprocess, sys
from datetime import datetime

WINDOW = 3.0      # seconds after a wake in which a PANEL_ALIVE=0 counts as a failed wake
REARM = 1.5       # a power-on within this time after a panel reset is part of the loop, not a new wake

ts_re = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})")
mode_re = re.compile(r"hwc_setPowerMode: Setting mode (\d) on display: 0")


def ts(line):
    m = ts_re.match(line)
    return datetime.strptime("2000-" + m.group(1), "%Y-%m-%d %H:%M:%S.%f") if m else None


def main():
    if len(sys.argv) > 1:
        p = subprocess.Popen(["cat", sys.argv[1]], stdout=subprocess.PIPE, text=True, errors="replace")
    else:
        p = subprocess.Popen(["adb", "logcat", "-v", "time", "-T", "1"], stdout=subprocess.PIPE,
                             text=True, errors="replace")
    last_esd = None
    wake = None          # [start_time, esd_count]
    results = []

    def finish():
        nonlocal wake
        if wake:
            start, n = wake
            verdict = "OK" if n == 0 else f"LOOP ({n} panel resets)"
            results.append(n == 0)
            ok = sum(results)
            print(f"{start:%H:%M:%S}  wake #{len(results):<3} {verdict:<24} total ok {ok}/{len(results)}", flush=True)
            wake = None

    print("watching... lock/unlock the phone now (Ctrl+C to stop)", flush=True)
    try:
        for line in p.stdout:
            t = ts(line)
            if t is None:
                continue
            if wake and (t - wake[0]).total_seconds() > WINDOW and not (
                    last_esd and (t - last_esd).total_seconds() < REARM):
                finish()
            if "PANEL_ALIVE=0" in line and "getPanelResetStatus" in line:
                if last_esd is None or (t - last_esd).total_seconds() > 0.1:   # two HWC processes log each event
                    if wake:
                        wake[1] += 1
                last_esd = t
                continue
            m = mode_re.search(line)
            if m and m.group(1) == "2":
                if last_esd and (t - last_esd).total_seconds() < REARM:
                    continue                      # driver re-powering the panel inside a loop
                finish()
                wake = [t, 0]
    except KeyboardInterrupt:
        pass
    finally:
        finish()
        p.terminate()
        n = len(results)
        if n:
            print(f"\nsummary: {sum(results)}/{n} wakes OK, {n - sum(results)} looped "
                  f"({100 * (n - sum(results)) / n:.0f}% failure)")


if __name__ == "__main__":
    main()
