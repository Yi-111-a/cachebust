"""Core analysis: prefix stability across LLM requests and volatile-content detection."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Patterns that commonly appear in prompts and change on every request,
# silently destroying prefix-based prompt caching (DeepSeek, OpenAI, Anthropic, vLLM...).
VOLATILE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("iso-timestamp", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b")),
    ("uuid", re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("unix-epoch", re.compile(r"\b1[5-9]\d{8}(\d{3})?\b")),
    ("clock-time", re.compile(r"\b\d{1,2}:\d{2}:\d{2}\b")),
    ("date", re.compile(r"\b\d{4}[-/]\d{2}[-/]\d{2}\b")),
    ("hex-id", re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{16,}\b")),
    ("request-id", re.compile(r"\b(req|run|sess|session|trace|msg)[_-][A-Za-z0-9]{6,}\b")),
]


def canonicalize(req: Any) -> str:
    """Turn one request body into the string a provider would see as the cacheable prefix.

    Order matters for caching: tools -> system -> messages. Key order inside
    JSON objects is preserved on purpose, because providers hash the bytes you send.
    """
    if isinstance(req, str):
        return req
    if not isinstance(req, dict):
        return json.dumps(req, ensure_ascii=False)
    body = req.get("body", req) if isinstance(req.get("body"), dict) else req
    parts: list[str] = []
    if body.get("tools") is not None:
        parts.append("[tools]" + json.dumps(body["tools"], ensure_ascii=False))
    if body.get("system") is not None:
        s = body["system"]
        parts.append("[system]" + (s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)))
    for m in body.get("messages") or []:
        role = m.get("role", "?") if isinstance(m, dict) else "?"
        content = m.get("content", "") if isinstance(m, dict) else m
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        parts.append(f"[{role}]" + content)
    if not parts and "prompt" in body:
        parts.append(str(body["prompt"]))
    return "\n".join(parts)


def common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def find_volatile(text: str, limit: int | None = None) -> list[dict]:
    region = text if limit is None else text[:limit]
    hits: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for name, pat in VOLATILE_PATTERNS:
        for m in pat.finditer(region):
            span = m.span()
            if any(s <= span[0] < e for s, e in seen):
                continue
            seen.add(span)
            hits.append({"kind": name, "offset": span[0], "match": m.group(0)})
    return sorted(hits, key=lambda h: h["offset"])


def lint_text(text: str, window: int = 4000) -> list[dict]:
    """Flag volatile content near the start of a prompt (where it hurts caching most)."""
    out = []
    for h in find_volatile(text, window):
        line = text.count("\n", 0, h["offset"]) + 1
        out.append({**h, "line": line})
    return out


def _tool_order_changed(prev: Any, cur: Any) -> bool:
    def names(r: Any) -> list[str]:
        if not isinstance(r, dict):
            return []
        body = r.get("body", r) if isinstance(r.get("body"), dict) else r
        res = []
        for t in body.get("tools") or []:
            if isinstance(t, dict):
                res.append(t.get("name") or (t.get("function") or {}).get("name") or json.dumps(t, sort_keys=True))
        return res
    a, b = names(prev), names(cur)
    return bool(a) and a != b and sorted(a) == sorted(b)


@dataclass
class Report:
    requests: int = 0
    total_chars: int = 0
    cacheable_chars: int = 0
    rows: list[dict] = field(default_factory=list)
    culprits: dict[str, int] = field(default_factory=dict)

    @property
    def hit_ratio(self) -> float:
        return self.cacheable_chars / self.total_chars if self.total_chars else 0.0

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "total_chars": self.total_chars,
            "cacheable_chars": self.cacheable_chars,
            "approx_total_tokens": self.total_chars // 4,
            "approx_cacheable_tokens": self.cacheable_chars // 4,
            "prefix_reuse_ratio": round(self.hit_ratio, 4),
            "culprits": self.culprits,
            "rows": self.rows,
        }


def analyze(requests: Iterable[Any], min_prefix: int = 0) -> Report:
    """Compare each request with the previous one and explain where the shared prefix breaks.

    min_prefix: providers only cache prefixes above a minimum size (e.g. ~1024 tokens);
    shared prefixes shorter than this many chars are counted as uncached.
    """
    rep = Report()
    prev_raw: Any = None
    prev = None
    for idx, raw in enumerate(requests):
        cur = canonicalize(raw)
        rep.requests += 1
        rep.total_chars += len(cur)
        row: dict = {"index": idx, "chars": len(cur), "shared_prefix": 0, "cause": "first-request"}
        if prev is not None:
            k = common_prefix_len(prev, cur)
            eff = k if k >= min_prefix else 0
            row["shared_prefix"] = eff
            rep.cacheable_chars += eff
            if k == len(prev):
                row["cause"] = "ok-append-only"
            else:
                cause = "content-changed"
                if _tool_order_changed(prev_raw, raw):
                    cause = "tool-order-changed"
                else:
                    lo = max(0, k - 40)
                    for h in find_volatile(cur[lo:k + 80]):
                        if lo + h["offset"] + len(h["match"]) > k:
                            cause = "volatile:" + h["kind"]
                            break
                row["cause"] = cause
                row["diverges_at"] = k
                row["context"] = cur[max(0, k - 30):k + 50]
                rep.culprits[cause] = rep.culprits.get(cause, 0) + 1
        rep.rows.append(row)
        prev, prev_raw = cur, raw
    return rep
