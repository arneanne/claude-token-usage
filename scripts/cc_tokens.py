#!/usr/bin/env python3
"""cc_tokens — count tokens used by Claude Code, from its local transcripts.

Claude Code writes every session to ~/.claude/projects/<project>/<session>.jsonl
(subagents under <session>/subagents/). Each assistant message carries the API
`usage` block; this script sums those, de-duplicating messages that are split
across several lines.

Cost is an estimate at Anthropic API list prices (see PRICES below). If you use
Claude Code on a Pro/Max subscription, that is what the usage *would* cost on
the API, not what you were billed.

Examples:
  cc_tokens.py                    # totals by day
  cc_tokens.py --by project       # by project folder
  cc_tokens.py --by model --days 7
  cc_tokens.py --by session --since 2026-10-01 --until 2026-10-22
  cc_tokens.py --project myapp    # only projects whose path contains "myapp"
  cc_tokens.py --json             # machine-readable output
  cc_tokens.py --prices           # show the price table used for costs
  cc_tokens.py --summary <id>     # JSON summary for the usage panel mod
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIELDS = ["input", "output", "cache_write", "cache_read"]

# USD per million tokens: (base input, output, cache-read multiplier of input).
# From https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-22).
# Cache writes cost 1.25x input (5-minute TTL) or 2x input (1-hour TTL).
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.025),
    "claude-mythos-5-1": (10.00, 50.00, 0.025),
    "claude-fable-5": (10.00, 50.00, 0.1),
    "claude-mythos-5": (10.00, 50.00, 0.1),
    "claude-opus-5-5": (4.00, 20.00, 0.05),
    "claude-opus-5": (5.00, 25.00, 0.1),
    "claude-opus-4-8": (5.00, 25.00, 0.1),
    "claude-opus-4-7": (5.00, 25.00, 0.1),
    "claude-opus-4-6": (5.00, 25.00, 0.1),
    "claude-opus-4-5": (5.00, 25.00, 0.1),
    "claude-opus-4-1": (15.00, 75.00, 0.1),
    "claude-opus-4": (15.00, 75.00, 0.1),
    "claude-sonnet-5-5": (2.00, 10.00, 0.05),
    "claude-sonnet-5": (2.00, 10.00, 0.1),
    "claude-sonnet-4-6": (3.00, 15.00, 0.1),
    "claude-sonnet-4-5": (3.00, 15.00, 0.1),
    "claude-sonnet-4": (3.00, 15.00, 0.1),
    "claude-haiku-5-5": (0.10, 0.50, 0.1),
    "claude-haiku-4-5": (1.00, 5.00, 0.1),
    "claude-3-5-haiku": (0.80, 4.00, 0.1),
}
# Claude Haiku 5.5 requests whose prompt (input + cache reads + writes) exceeds 100K.
HAIKU_55_LONG = (0.50, 2.50, 0.1)
# Fast mode replaces base input/output; cache multipliers apply on top.
FAST_PRICES = {
    "claude-opus-5-5": (8.00, 40.00),
    "claude-opus-5": (10.00, 50.00),
    "claude-opus-4-8": (10.00, 50.00),
}
US_GEO_MULTIPLIER = 1.1  # inference_geo "us" on Claude 4.6+ models
WEB_SEARCH_USD = 10.00 / 1000


def price_key(model):
    """Map a model id (possibly dated or provider-prefixed) to a PRICES key."""
    m = model.lower().split("/")[-1].removeprefix("anthropic.").split("[")[0].split("@")[0]
    for key in sorted(PRICES, key=len, reverse=True):
        if m == key or m.startswith(key + "-"):
            return key
    return None


def cost_of(r):
    """USD cost of one message, or None if the model has no known price."""
    key = price_key(r["model"])
    if key is None:
        return None
    inp, out, read_mult = PRICES[key]
    prompt = r["input"] + r["cache_write"] + r["cache_read"]
    if key == "claude-haiku-5-5" and prompt > 100_000:
        inp, out, read_mult = HAIKU_55_LONG
    if r["speed"] == "fast" and key in FAST_PRICES:
        inp, out = FAST_PRICES[key]
    usd = (
        r["input"] * inp
        + r["write_5m"] * inp * 1.25
        + r["write_1h"] * inp * 2.0
        + r["cache_read"] * inp * read_mult
        + r["output"] * out
    ) / 1_000_000
    if r["geo"] == "us":
        usd *= US_GEO_MULTIPLIER
    return usd + r["web_searches"] * WEB_SEARCH_USD


def default_roots():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    roots = [Path(p) / "projects" for p in env.split(",")] if env else []
    roots.append(Path.home() / ".claude" / "projects")
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def project_name(path, root):
    rel = path.relative_to(root)
    return rel.parts[0]


def iter_usage(roots):
    """Yield one record per unique assistant API message.

    A message streamed as several content blocks is written as several lines
    sharing message.id; output_tokens grows across them, so keep the largest.
    """
    records = {}
    for root in roots:
        for path in root.rglob("*.jsonl"):
            try:
                fh = path.open(encoding="utf-8")
            except OSError:
                continue
            with fh:
                for n, line in enumerate(fh):
                    if '"usage"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = d.get("message")
                    if d.get("type") != "assistant" or not isinstance(msg, dict):
                        continue
                    u = msg.get("usage")
                    model = msg.get("model") or "unknown"
                    if not u or model == "<synthetic>":
                        continue
                    try:
                        when = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).astimezone()
                    except (KeyError, AttributeError, ValueError):
                        continue
                    cw = u.get("cache_creation_input_tokens", 0) or 0
                    split = u.get("cache_creation") or {}
                    w1h = split.get("ephemeral_1h_input_tokens", 0) or 0
                    stu = u.get("server_tool_use") or {}
                    rec = {
                        "when": when,
                        "model": model,
                        "project": d.get("cwd") or project_name(path, root),
                        "session": d.get("sessionId") or path.stem,
                        "subagent": "subagents" in path.parts,
                        "input": u.get("input_tokens", 0) or 0,
                        "output": u.get("output_tokens", 0) or 0,
                        "cache_write": cw,
                        "cache_read": u.get("cache_read_input_tokens", 0) or 0,
                        "write_1h": w1h,
                        "write_5m": cw - w1h,
                        "speed": u.get("speed"),
                        "geo": u.get("inference_geo"),
                        "web_searches": stu.get("web_search_requests", 0) or 0,
                    }
                    key = msg.get("id") or (str(path), n)
                    prev = records.get(key)
                    if prev is None or rec["output"] > prev["output"]:
                        records[key] = rec
    return records.values()


def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def fmt(n):
    return f"{n:,}"


def fmt_usd(x):
    return f"${x:,.2f}"


def print_prices():
    print(f"{'Model':<20}  {'Input':>7}  {'5m write':>8}  {'1h write':>8}  {'Cache read':>10}  {'Output':>7}   (USD / MTok)")
    for k, (i, o, rm) in PRICES.items():
        print(f"{k:<20}  {i:>7.2f}  {i*1.25:>8.3f}  {i*2:>8.2f}  {i*rm:>10.3f}  {o:>7.2f}")
    print("\nHaiku 5.5 prompts over 100K tokens: input 0.50, output 2.50 (cache multipliers as usual).")
    print("Fast mode: " + ", ".join(f"{k} {i:g}/{o:g}" for k, (i, o) in FAST_PRICES.items()) + " (input/output).")
    print("US-only inference (inference_geo=us): x1.1. Web search: $10 per 1,000 searches.")


def short(s, width):
    return s if len(s) <= width else "…" + s[-(width - 1):]


def assign_projects(records, subfolders=False):
    """Set r["group"]: the folder a session started in, folded into its
    outermost parent project (so myapp/src/lib counts under myapp)."""
    start = {}
    for r in sorted(records, key=lambda r: r["when"]):
        start.setdefault(r["session"], r["project"])
    home = str(Path.home())
    roots = sorted({p for p in start.values() if p != home}, key=len)

    def outer(p):
        for root in roots:
            if p == root or p.startswith(root.rstrip("/") + "/"):
                return root
        return p

    for r in records:
        r["group"] = r["project"] if subfolders else outer(start[r["session"]])


def group_rows(records, by):
    """Sum records into rows keyed by `by`; returns (rows, totals, unpriced)."""
    groups = defaultdict(lambda: dict.fromkeys(FIELDS + ["messages", "cost"], 0))
    first_seen, project_of, unpriced = {}, {}, defaultdict(int)
    for r in records:
        key = {
            "day": r["when"].date().isoformat(),
            "month": r["when"].strftime("%Y-%m"),
            "project": r["group"],
            "model": r["model"],
            "session": r["session"],
        }[by]
        g = groups[key]
        for f in FIELDS:
            g[f] += r[f]
        g["messages"] += 1
        c = cost_of(r)
        if c is None:
            unpriced[r["model"]] += 1
        else:
            g["cost"] += c
        if key not in first_seen or r["when"] < first_seen[key]:
            first_seen[key] = r["when"]
            project_of[key] = r["group"]

    rows = []
    for key, g in groups.items():
        row = {by: key, **g, "cost": round(g["cost"], 4), "total": sum(g[f] for f in FIELDS)}
        if by == "session":
            row["started"] = first_seen[key].strftime("%Y-%m-%d %H:%M")
            row["project"] = project_of[key]
        rows.append(row)
    if by in ("day", "month"):
        rows.sort(key=lambda x: x[by])
    elif by == "session":
        rows.sort(key=lambda x: x["started"])
    else:
        rows.sort(key=lambda x: -x["cost"])
    totals = {f: sum(r[f] for r in rows) for f in FIELDS + ["messages", "total"]}
    totals["cost"] = round(sum(r["cost"] for r in rows), 4)
    return rows, totals, unpriced


def summary(records, session_id):
    """Everything the usage panel shows, in one JSON object."""
    today = datetime.now().date()

    def since(days):
        return [r for r in records if (today - r["when"].date()).days < days]

    def total(recs):
        return group_rows(recs, "model")[1]

    daily = {row["day"]: row for row in group_rows(since(14), "day")[0]}
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": total([r for r in records if r["session"] == session_id]),
        "today": total(since(1)),
        "week": total(since(7)),
        "month": total(since(30)),
        "all": total(records),
        "daily": [
            {"day": d.isoformat(), "cost": daily.get(d.isoformat(), {}).get("cost", 0)}
            for d in (today - timedelta(days=n) for n in range(13, -1, -1))
        ],
        "by_model": group_rows(since(30), "model")[0],
        "by_project": group_rows(since(30), "project")[0][:8],
    }


def main():
    ap = argparse.ArgumentParser(description="Count Claude Code token usage from local transcripts.")
    ap.add_argument("--by", choices=["day", "month", "project", "model", "session"], default="day")
    ap.add_argument("--since", type=parse_date, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--until", type=parse_date, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--days", type=int, help="only the last N days (including today)")
    ap.add_argument("--project", help="only projects whose path contains this text")
    ap.add_argument("--model", help="only models whose name contains this text")
    ap.add_argument("--subfolders", action="store_true", help="--by project: keep each working folder separate")
    ap.add_argument("--no-subagents", action="store_true", help="exclude subagent transcripts")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--dir", action="append", type=Path, help="transcript root(s); default ~/.claude/projects")
    ap.add_argument("--prices", action="store_true", help="print the price table used for cost and exit")
    ap.add_argument("--summary", metavar="SESSION_ID", help="print the usage panel's JSON summary and exit")
    a = ap.parse_args()
    if a.prices:
        print_prices()
        return

    roots = a.dir or default_roots()
    if not roots:
        sys.exit("No Claude Code transcript folder found (looked for ~/.claude/projects).")
    records = list(iter_usage(roots))
    assign_projects(records, a.subfolders)
    if a.summary:
        print(json.dumps(summary(records, a.summary)))
        return

    since = a.since
    if a.days:
        since = max(since or parse_date("1970-01-01"), datetime.now().date() - timedelta(days=a.days - 1))
    records = [
        r for r in records
        if not (since and r["when"].date() < since)
        and not (a.until and r["when"].date() > a.until)
        and not (a.project and a.project.lower() not in r["group"].lower())
        and not (a.model and a.model.lower() not in r["model"].lower())
        and not (a.no_subagents and r["subagent"])
    ]
    rows, totals, unpriced = group_rows(records, a.by)
    if unpriced:
        print("Note: no price known for " + ", ".join(f"{m} ({n} msgs)" for m, n in unpriced.items())
              + "; their cost is counted as $0.", file=sys.stderr)

    if a.json:
        print(json.dumps({"rows": rows, "totals": totals}, indent=2))
        return
    if not rows:
        print("No usage found for those filters.")
        return

    label_w = {"day": 10, "month": 7, "model": 22, "session": 48}.get(a.by, 48)
    cols = ["input", "output", "cache_write", "cache_read", "total", "messages", "cost"]
    heads = ["Input", "Output", "Cache write", "Cache read", "Total", "Msgs", "Cost (USD)"]
    show = lambda c, v: fmt_usd(v) if c == "cost" else fmt(v)
    widths = [max(len(h), len(show(c, totals[c]))) for c, h in zip(cols, heads)]

    def line(label, vals):
        return f"{label:<{label_w}}  " + "  ".join(f"{v:>{w}}" for v, w in zip(vals, widths))

    rule = "-" * (label_w + 2 + sum(widths) + 2 * (len(widths) - 1))
    print(line(a.by.capitalize(), heads))
    print(rule)
    for r in rows:
        label = str(r[a.by])
        if a.by == "session":
            label = f"{r['started']}  {r[a.by][:8]}  {Path(r['project']).name}"
            label = label[:label_w]
        print(line(short(label, label_w), [show(c, r[c]) for c in cols]))
    print(rule)
    print(line("TOTAL", [show(c, totals[c]) for c in cols]))


if __name__ == "__main__":
    main()
