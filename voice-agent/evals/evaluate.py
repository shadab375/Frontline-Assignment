#!/usr/bin/env python3
"""Offline, provider-free regression evaluation for the voice-agent contract.

This validates observable assistant turns (spoken text + tool calls) against the
high-risk call policy.  It deliberately does not call an LLM or production tool.
Use it with captured model turns before changing the prompt or tool wiring.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


@dataclass
class Finding:
    code: str
    message: str


def _calls(turn: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [call for call in turn.get("calls", []) if call["name"] == name]


def _has_holding_text(text: str) -> bool:
    return bool(re.search(r"\b(let me|one moment|one second|checking)\b", text, re.I))


def _number_in(text: str, amount: int | float) -> bool:
    # Accept phone-friendly $1,850 and 1850, but never infer a value from words.
    value = f"{amount:,.0f}"
    return value in text or value.replace(",", "") in text


def evaluate_case(case: dict[str, Any]) -> list[Finding]:
    """Return policy failures for one scripted, observable conversation."""
    findings: list[Finding] = []
    state = {"verified": False, "loaded": False, "agreed_price": None}

    for index, turn in enumerate(case["turns"], start=1):
        caller = turn.get("caller", "")
        text = turn.get("response", "")
        calls = turn.get("calls", [])
        for call in calls:
            if "name" not in call or "arguments" not in call:
                findings.append(Finding("malformed_call", f"turn {index}: call needs name and arguments"))

        expected = turn.get("expect", {})
        for name in expected.get("calls", []):
            if not _calls(turn, name):
                findings.append(Finding("missing_tool", f"turn {index}: expected {name}"))
        for name in expected.get("no_calls", []):
            if _calls(turn, name):
                findings.append(Finding("premature_tool", f"turn {index}: must not call {name}"))

        for name, arguments in expected.get("arguments", {}).items():
            found = _calls(turn, name)
            if not found or not all(found[0]["arguments"].get(k) == v for k, v in arguments.items()):
                findings.append(Finding("wrong_arguments", f"turn {index}: {name} arguments differ from expected"))

        if expected.get("holding") and calls and not _has_holding_text(text):
            findings.append(Finding("silent_tool_call", f"turn {index}: tool call lacks a spoken holding phrase"))

        for forbidden in expected.get("forbidden_amounts", []):
            if _number_in(text, forbidden):
                findings.append(Finding("confidential_rate_leak", f"turn {index}: response exposes ${forbidden:,.0f}"))

        if expected.get("ask_contact") and not re.search(r"(phone|number).*(name|contact)|(name|contact).*(phone|number)", text, re.I):
            findings.append(Finding("missing_contact_collection", f"turn {index}: agreement requires name and phone request"))
        if "explicit_price" in expected and not _number_in(text, expected["explicit_price"]):
            findings.append(Finding("price_not_confirmed", f"turn {index}: response must explicitly confirm ${expected['explicit_price']:,.0f}"))

        if _calls(turn, "verify_carrier") and turn.get("tool_result", {}).get("verify_carrier") == "success":
            state["verified"] = True
        if _calls(turn, "get_load_context") and turn.get("tool_result", {}).get("get_load_context") == "success":
            state["loaded"] = True
        if _calls(turn, "record_agreement"):
            state["agreed_price"] = _calls(turn, "record_agreement")[0]["arguments"].get("agreed_price")
        if _calls(turn, "transfer_to_human") and not (state["verified"] and state["loaded"]):
            findings.append(Finding("unsafe_transfer", f"turn {index}: transfer requires verified carrier and loaded context"))
        if _calls(turn, "end_call") and turn.get("expect", {}).get("requires_record") and state["agreed_price"] is None:
            findings.append(Finding("unrecorded_close", f"turn {index}: agreement close occurred before record_agreement"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "cases.json")
    parser.add_argument("--report", type=Path, default=ROOT / "latest-report.json")
    args = parser.parse_args()
    corpus = json.loads(args.cases.read_text())
    results = []
    for case in corpus["cases"]:
        findings = evaluate_case(case)
        results.append({"id": case["id"], "risk": case["risk"], "status": "pass" if not findings else "fail", "findings": [f.__dict__ for f in findings]})
    summary = {"passed": sum(r["status"] == "pass" for r in results), "failed": sum(r["status"] == "fail" for r in results)}
    report = {"suite": "frontline-offline-policy-traces", "mode": "offline-fixture", "summary": summary, "results": results,
              "limitations": "Fixtures validate observed turns, not live speech recognition, model nondeterminism, or provider integrations."}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
