# cachebust

[![CI](https://github.com/Yi-111-a/cachebust/actions/workflows/ci.yml/badge.svg)](https://github.com/Yi-111-a/cachebust/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Find what's busting your LLM prompt cache.**

DeepSeek, OpenAI, Anthropic, Gemini and vLLM all bill cached prompt prefixes at a fraction of the normal input price. All of them match on an *exact* prefix, though. A single `Current time: 2026-10-14T02:11:37Z`, a session UUID, or tools sent in a different order near the top of your prompt breaks the cache for every token after it, and nothing tells you it happened.

`cachebust` is a zero-dependency Python CLI that:

- **analyze**: replays a JSONL log of your request bodies (in send order), measures how much of each request reuses the previous request's prefix, and tells you **where and why** the prefix broke (`volatile:iso-timestamp`, `volatile:uuid`, `tool-order-changed`, `content-changed`...). It can also estimate what you pay against the append-only ideal.
- **lint**: scans prompt or template files for volatile content (timestamps, dates, UUIDs, epochs, hex IDs, request IDs) near the start, which is where it costs the most.

Works with OpenAI or DeepSeek style `{"messages": [...], "tools": [...]}` bodies, Anthropic style `{"system": ..., "messages": ...}`, or `{"body": {...}}` wrappers.

## Install

```bash
pip install git+https://github.com/Yi-111-a/cachebust
```

## Usage

```console
$ cachebust analyze examples/requests.jsonl --price-input 0.28 --price-cached 0.028
requests: 3  prefix reuse: 47.4%  (~51 of ~109 tokens)
estimated input cost: $1.8e-05  (append-only ideal: $1e-05)
  #1: cache breaks at char 62 [volatile:iso-timestamp] ...' Current time: 2026-10-14T02:11:37Z. Follow the rules below carefully.\n[user]hi\n'
culprits: volatile:iso-timestamp x1

$ cachebust lint examples/system_prompt.txt
examples/system_prompt.txt:2: uuid: '3f2b8c1e-...' breaks prompt caching for everything after it
examples/system_prompt.txt:3: date: '2026-10-14' breaks prompt caching for everything after it
```

Options:

| flag | meaning |
|---|---|
| `--json` | machine-readable report |
| `--min-prefix N` | count shared prefixes shorter than N chars as uncached (providers have minimums, e.g. ~1024 tokens) |
| `--fail-under 0.8` | exit 1 if prefix reuse < 80%. Useful as a CI gate on recorded agent traces |
| `--price-input` / `--price-cached` | USD per 1M tokens, so you can estimate cost (look up your provider's current prices) |

`lint` exits 1 when it finds something, so you can drop it into pre-commit or CI.

### Getting a log

Log the exact JSON body you send. For example, with the OpenAI Python SDK, wrap `client.chat.completions.create` and append `json.dumps(kwargs)` to a `.jsonl` file. Token counts are approximate (chars / 4). The goal is to find the break points, not to do exact accounting.

### Fixing what it finds

- Move timestamps, IDs and per-user data to the **end** (the last user message) instead of the system prompt.
- Sort tool definitions deterministically, and serialize JSON with stable key order.
- Keep conversation history append-only: don't rewrite or summarize earlier turns on every call.

## 中文简介

`cachebust` 用来排查 LLM 提示词缓存（DeepSeek、OpenAI、Anthropic 等的前缀缓存）为什么没有命中。缓存要求前缀逐字节一致，系统提示里放一个当前时间、会话 UUID，或者工具顺序变了，后面所有 token 都得按全价计费。

- `cachebust analyze 请求日志.jsonl`：按发送顺序比较相邻请求，算出前缀复用率，指出缓存在哪个位置、因为什么断掉，还可以估算费用。
- `cachebust lint 提示词.txt`：检查提示词开头有没有时间戳、日期、UUID 这类每次都会变的内容。

零依赖，Python 3.9 及以上，MIT 许可。

## License

MIT
