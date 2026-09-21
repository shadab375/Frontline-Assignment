# Frontline voice-agent evaluations

Run entirely offline from this directory (Python 3.11; no packages, credentials, network, database, phone number, or model required):

```bash
python3 evaluate.py
python3 -m unittest -v test_evaluate.py
```

`evaluate.py` writes `latest-report.json` and exits non-zero when a policy trace fails. The report lists the scenario, product risk, and machine-readable failure code, such as `confidential_rate_leak`, `silent_tool_call`, or `unsafe_transfer`.

The corpus represents expected observable turns: caller transcript, spoken response, tool calls/arguments, and (where needed) mocked tool results. To evaluate a prompt/model change, replace or add cases with captured model turns from a deterministic replay harness, then run this command in CI. Do not point this runner at production tools; its purpose is a safe gate before a separately authorized development-call qualification.

See [design.md](design.md) for scope, thresholds, and known blind spots.
