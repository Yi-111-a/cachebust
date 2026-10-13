from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import analyze, lint_text


def _load_jsonl(path: str) -> list:
    fh = sys.stdin if path == "-" else open(path, encoding="utf-8")
    out = []
    with fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{n}: invalid JSON: {e}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cachebust", description="Find what's busting your LLM prompt cache.")
    p.add_argument("--version", action="version", version=f"cachebust {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyze a JSONL log of request bodies, in send order")
    a.add_argument("file", help="JSONL file (or - for stdin)")
    a.add_argument("--min-prefix", type=int, default=0, help="ignore shared prefixes shorter than N chars")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.add_argument("--fail-under", type=float, default=None, help="exit 1 if prefix reuse ratio < X (0-1)")
    a.add_argument("--price-input", type=float, default=None, help="USD per 1M uncached input tokens")
    a.add_argument("--price-cached", type=float, default=None, help="USD per 1M cached input tokens")

    l = sub.add_parser("lint", help="flag volatile content (timestamps, UUIDs...) near the top of a prompt file")
    l.add_argument("files", nargs="+")
    l.add_argument("--window", type=int, default=4000, help="chars from the start to check (default 4000)")

    args = p.parse_args(argv)

    if args.cmd == "lint":
        bad = 0
        for f in args.files:
            with open(f, encoding="utf-8") as fh:
                for h in lint_text(fh.read(), args.window):
                    bad += 1
                    print(f"{f}:{h['line']}: {h['kind']}: {h['match']!r} breaks prompt caching for everything after it")
        if not bad:
            print("no volatile content found")
        return 1 if bad else 0

    rep = analyze(_load_jsonl(args.file), min_prefix=args.min_prefix)
    d = rep.to_dict()
    if args.price_input is not None and args.price_cached is not None:
        unc = (d["approx_total_tokens"] - d["approx_cacheable_tokens"]) / 1e6
        cached = d["approx_cacheable_tokens"] / 1e6
        actual = unc * args.price_input + cached * args.price_cached
        ideal_cached = sum(r["chars"] for r in rep.rows[1:]) // 4 / 1e6
        ideal = (d["approx_total_tokens"] / 1e6 - ideal_cached) * args.price_input + ideal_cached * args.price_cached
        d["estimated_cost_usd"] = round(actual, 6)
        d["estimated_cost_if_append_only_usd"] = round(ideal, 6)
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(f"requests: {d['requests']}  prefix reuse: {d['prefix_reuse_ratio']:.1%}  "
              f"(~{d['approx_cacheable_tokens']:,} of ~{d['approx_total_tokens']:,} tokens)")
        if "estimated_cost_usd" in d:
            print(f"estimated input cost: ${d['estimated_cost_usd']}  (append-only ideal: ${d['estimated_cost_if_append_only_usd']})")
        for r in rep.rows:
            if r["cause"] not in ("first-request", "ok-append-only"):
                print(f"  #{r['index']}: cache breaks at char {r['diverges_at']} [{r['cause']}] ...{r['context']!r}")
        if d["culprits"]:
            print("culprits: " + ", ".join(f"{k} x{v}" for k, v in sorted(d["culprits"].items(), key=lambda x: -x[1])))
    if args.fail_under is not None and rep.hit_ratio < args.fail_under:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
