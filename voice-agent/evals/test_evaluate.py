"""Self-tests prove the evaluator catches its most important failure modes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from evaluate import evaluate_case


class EvaluatorFailureDetectionTests(unittest.TestCase):
    def test_detects_confidential_rate_leak(self):
        findings = evaluate_case({"id": "bad", "turns": [{"caller": "budget?", "response": "Our max is 2000", "calls": [], "expect": {"forbidden_amounts": [2000]}}]})
        self.assertIn("confidential_rate_leak", [f.code for f in findings])

    def test_detects_transfer_before_verification_and_load(self):
        findings = evaluate_case({"id": "bad", "turns": [{"caller": "human", "response": "One moment", "calls": [{"name": "transfer_to_human", "arguments": {}}], "expect": {}}]})
        self.assertIn("unsafe_transfer", [f.code for f in findings])


if __name__ == "__main__":
    unittest.main()
