#!/usr/bin/env python3
"""Summarise the guard's decision log: where delegated work went, and what was blocked.

Usage: python3 stats.py [--days N]
Standard library only; works on Python 3.8+.
"""
import argparse
import collections
import json
import os
import sys
import time

LOG = os.path.join(os.path.expanduser("~"), ".claude", "model-planner", "guard.log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - args.days * 86400))

    rows = []
    for path in (LOG + ".1", LOG):
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and str(row.get("ts", "")) >= cutoff:
                    rows.append(row)

    if not rows:
        print("No delegations logged in the last %d days (%s)." % (args.days, LOG))
        return 0

    allowed = [r for r in rows if str(r.get("decision", "")).startswith("allow")]
    denied = [r for r in rows if r.get("decision") == "deny"]
    by_model = collections.Counter(r.get("effective") or "inherits session model" for r in allowed)
    by_signal = collections.Counter(s for r in denied for s in (r.get("signals") or []))

    print("Delegations in the last %d days: %d  (allowed %d, blocked %d)" % (args.days, len(rows), len(allowed), len(denied)))
    print("\nAllowed, by model:")
    for model, n in by_model.most_common():
        print("  %-24s %4d  %3d%%" % (model, n, round(100.0 * n / len(allowed))))
    if denied:
        print("\nBlocked and sent to a higher tier, by reason:")
        for signal, n in by_signal.most_common():
            print("  %-24s %4d" % (signal, n))
    print("\nNote: this counts delegated work only. The main session's own usage is not routed and is not shown here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
