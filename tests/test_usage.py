"""The token-usage capture seam: record() accrues into the active collect()
block, is a no-op outside one, and independent blocks stay isolated."""

import unittest

from text2sql.trace import usage
from text2sql.trace.usage import LlmCall


class _Usage:  # mimics anthropic resp.usage
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _Resp:
    def __init__(self, i, o):
        self.usage = _Usage(i, o)


class TestUsageCollector(unittest.TestCase):
    def test_record_accrues_inside_collect(self):
        with usage.collect() as calls:
            usage.record(LlmCall("plan", "m", 10, 5))
            usage.record(LlmCall("summary", "m", 3, 2))
        self.assertEqual([c.role for c in calls], ["plan", "summary"])
        self.assertEqual(usage.totals(calls), (13, 7))

    def test_record_outside_collect_is_noop(self):
        usage.record(LlmCall("plan", "m", 1, 1))  # must not raise, nowhere to go

    def test_record_usage_reads_response_usage(self):
        with usage.collect() as calls:
            usage.record_usage("plan", "opus", _Resp(100, 40), ms=12.5)
        self.assertEqual(len(calls), 1)
        c = calls[0]
        self.assertEqual((c.role, c.model, c.input_tokens, c.output_tokens), ("plan", "opus", 100, 40))
        self.assertEqual(c.ms, 12.5)

    def test_record_usage_tolerates_missing_usage(self):
        with usage.collect() as calls:
            usage.record_usage("plan", "m", object())  # no .usage attribute
        self.assertEqual(calls, [])

    def test_independent_collect_blocks_are_isolated(self):
        with usage.collect() as first:
            usage.record(LlmCall("plan", "m", 1, 1))
            with usage.collect() as second:
                usage.record(LlmCall("summary", "m", 2, 2))
            self.assertEqual([c.role for c in second], ["summary"])
            self.assertEqual([c.role for c in first], ["plan"])


if __name__ == "__main__":
    unittest.main()
