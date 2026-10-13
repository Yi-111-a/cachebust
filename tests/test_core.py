import json, subprocess, sys, unittest
from pathlib import Path
from cachebust import analyze, canonicalize, common_prefix_len, lint_text
from cachebust.cli import main

ROOT = Path(__file__).resolve().parent.parent


def req(system, *msgs, tools=None):
    r = {"messages": [{"role": "system", "content": system}] + [{"role": "user", "content": m} for m in msgs]}
    if tools:
        r["tools"] = tools
    return r


class T(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(common_prefix_len("abcd", "abxy"), 2)
        self.assertEqual(common_prefix_len("", "a"), 0)

    def test_append_only_is_fully_reused(self):
        a, b = req("sys", "one"), req("sys", "one", "two")
        rep = analyze([a, b])
        self.assertEqual(rep.rows[1]["cause"], "ok-append-only")
        self.assertEqual(rep.cacheable_chars, len(canonicalize(a)))

    def test_timestamp_detected(self):
        rep = analyze([req("t=2026-10-14T02:10:00Z rules", "x"), req("t=2026-10-14T02:11:37Z rules", "x")])
        self.assertEqual(rep.rows[1]["cause"], "volatile:iso-timestamp")

    def test_uuid_detected(self):
        rep = analyze([req("id 3f2b8c1e-9a4d-4e2f-8b7a-1c2d3e4f5a6b", "x"),
                       req("id 9f2b8c1e-9a4d-4e2f-8b7a-1c2d3e4f5a6c", "x")])
        self.assertEqual(rep.rows[1]["cause"], "volatile:uuid")

    def test_tool_order(self):
        t1 = [{"type": "function", "function": {"name": "a"}}, {"type": "function", "function": {"name": "b"}}]
        rep = analyze([req("s", "x", tools=t1), req("s", "x", tools=t1[::-1])])
        self.assertEqual(rep.rows[1]["cause"], "tool-order-changed")

    def test_plain_change(self):
        rep = analyze([req("hello world", "x"), req("hello there", "x")])
        self.assertEqual(rep.rows[1]["cause"], "content-changed")

    def test_min_prefix(self):
        rep = analyze([req("s", "a"), req("s", "a", "b")], min_prefix=10_000)
        self.assertEqual(rep.cacheable_chars, 0)

    def test_anthropic_system(self):
        r = {"system": "S", "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
        self.assertTrue(canonicalize(r).startswith("[system]S"))

    def test_lint(self):
        hits = lint_text((ROOT / "examples/system_prompt.txt").read_text())
        kinds = {h["kind"] for h in hits}
        self.assertIn("uuid", kinds)
        self.assertIn("date", kinds)
        self.assertEqual(lint_text("plain prompt"), [])

    def test_cli_json(self):
        out = subprocess.run([sys.executable, "-m", "cachebust", "analyze", "--json", "--price-input", "0.28",
                              "--price-cached", "0.028", str(ROOT / "examples/requests.jsonl")],
                             capture_output=True, text=True, cwd=ROOT, check=True)
        d = json.loads(out.stdout)
        self.assertEqual(d["requests"], 3)
        self.assertEqual(d["culprits"], {"volatile:iso-timestamp": 1})
        self.assertLess(d["estimated_cost_if_append_only_usd"], d["estimated_cost_usd"])

    def test_cli_fail_under(self):
        self.assertEqual(main(["analyze", "--fail-under", "0.99", str(ROOT / "examples/requests.jsonl")]), 1)
        self.assertEqual(main(["lint", str(ROOT / "examples/system_prompt.txt")]), 1)


if __name__ == "__main__":
    unittest.main()
