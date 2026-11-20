#!/usr/bin/env python3
"""sum_usage.py — emit a WINDOW_LEDGER-ready api_usage row for a loop run.

Sums per-message usage fields across agent transcript JSONLs (the only reliable
api_usage source; harness subagent_tokens is an opaque separate series — never mix).

Usage (from the session that ran the workflow):
  python sum_usage.py <session_dir_or_subagents_dir> [--since "2026-08-25 11:12"] [--until "..."]
                      [--files agent-a.jsonl agent-b.jsonl ...] [--row SESSION RUN_ID EVENT]

  <dir>    a project session dir (…/projects/<slug>/<session-id>) or its subagents/ subdir
  --since/--until   filter agent files by mtime (local time) — bracket your run window
  --files  explicit agent JSONLs instead of a directory sweep
  --row    also print a ready-to-append ledger CSV row (v1.0.4 columns)

Output: per-file and total in/out/cache_write/cache_read; raw = in+out+cache_write.
Convention: cache_read reported separately (approx. unmetered on the 5-h window, n=2).
"""
import argparse, json, os, sys, glob
from datetime import datetime

def parse_ts(s):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    sys.exit(f"bad timestamp: {s!r} (want 'YYYY-MM-DD HH:MM')")

def sum_file(path):
    t = dict(inp=0, out=0, cw=0, cr=0, msgs=0)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try: d = json.loads(line)
                except json.JSONDecodeError: continue
                u = (d.get("message") or {}).get("usage") if isinstance(d.get("message"), dict) else None
                if not u: continue
                t["inp"] += u.get("input_tokens", 0) or 0
                t["out"] += u.get("output_tokens", 0) or 0
                t["cw"]  += u.get("cache_creation_input_tokens", 0) or 0
                t["cr"]  += u.get("cache_read_input_tokens", 0) or 0
                t["msgs"] += 1
    except OSError as e:
        print(f"  SKIP {path}: {e}", file=sys.stderr)
    return t

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", nargs="?", help="session dir or subagents dir")
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--until", default=None)
    ap.add_argument("--row", nargs=3, metavar=("SESSION", "RUN_ID", "EVENT"), default=None)
    a = ap.parse_args()

    if a.files:
        files = a.files
    else:
        if not a.dir: sys.exit("give a directory or --files")
        d = a.dir
        sub = os.path.join(d, "subagents")
        if os.path.isdir(sub): d = sub
        files = sorted(glob.glob(os.path.join(d, "agent-*.jsonl")))
        if not files: sys.exit(f"no agent-*.jsonl under {d}")
    since = parse_ts(a.since) if a.since else None
    until = parse_ts(a.until) if a.until else None
    if since or until:
        kept = []
        for f in files:
            m = datetime.fromtimestamp(os.path.getmtime(f))
            if since and m < since: continue
            if until and m > until: continue
            kept.append(f)
        files = kept
        if not files: sys.exit("no agent files in the given mtime window")

    T = dict(inp=0, out=0, cw=0, cr=0, msgs=0)
    print(f"{'file':<44}{'in':>9}{'out':>9}{'cache_w':>12}{'cache_r':>12}{'raw':>12}")
    for f in files:
        t = sum_file(f)
        raw = t["inp"] + t["out"] + t["cw"]
        if t["msgs"] == 0:
            print(f"{os.path.basename(f):<44}{'—— no usage fields (Agent-tool transcript?) ——':>54}")
            continue
        print(f"{os.path.basename(f):<44}{t['inp']:>9}{t['out']:>9}{t['cw']:>12}{t['cr']:>12}{raw:>12}")
        for k in T: T[k] += t[k]
    RAW = T["inp"] + T["out"] + T["cw"]
    print("-" * 98)
    print(f"{'TOTAL (' + str(len(files)) + ' files)':<44}{T['inp']:>9}{T['out']:>9}{T['cw']:>12}{T['cr']:>12}{RAW:>12}")
    print(f"\nraw (in+out+cache_write) = {RAW:,}   cache_read = {T['cr']:,}   (~{RAW/250000:.1f}pp of a window at 0.25M/pp)")
    if a.row:
        s, r, e = a.row
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f'\nledger row:\n{ts},{s},{r},{e},api_usage,{RAW},{T["cr"]},,"sum_usage.py over {len(files)} agent files"')

if __name__ == "__main__":
    main()
