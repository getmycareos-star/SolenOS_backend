#!/usr/bin/env bash
set -Eeuo pipefail

python -m pytest backend/tests -q

python -m pytest backend/tests/test_uncertainty_benchmark.py -q --junitxml=/tmp/uncertainty-benchmark.xml
python - <<'PY'
import xml.etree.ElementTree as ET
root = ET.parse("/tmp/uncertainty-benchmark.xml").getroot()
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
tests = sum(int(s.attrib.get("tests", 0)) for s in suites)
failures = sum(int(s.attrib.get("failures", 0)) for s in suites)
errors = sum(int(s.attrib.get("errors", 0)) for s in suites)
skipped = sum(int(s.attrib.get("skipped", 0)) for s in suites)
executed = tests - skipped
passed = executed - failures - errors
score = round(100 * passed / executed, 2) if executed else 0.0
print(f"UNCERTAINTY_BENCHMARK_SCORE={score:.2f}")
print(f"UNCERTAINTY_BENCHMARK_PASSED={passed}")
print(f"UNCERTAINTY_BENCHMARK_EXECUTED={executed}")
print(f"UNCERTAINTY_BENCHMARK_FAILED={failures + errors}")
print(f"UNCERTAINTY_BENCHMARK_SKIPPED={skipped}")
if not executed or failures or errors or skipped:
    raise SystemExit("Benchmark gate failed: zero executed, failing/error cases, or skipped cases.")
PY

python -m pytest backend/tests/test_longitudinal_change_benchmark.py -q -s | tee /tmp/longitudinal-change-benchmark.log
python - <<'PY'
import json
from pathlib import Path
output = Path("/tmp/longitudinal-change-benchmark.log").read_text()
marker = "LONGITUDINAL_CHANGE_BENCHMARK="
reports = [line.split(marker, 1)[1] for line in output.splitlines() if marker in line]
if not reports:
    raise SystemExit("Longitudinal benchmark did not publish its measured case report.")
report = json.loads(reports[-1])
passed = int(report.get("cases_passed", 0))
total = int(report.get("cases_total", 0))
score = float(report.get("score", 0.0))
print(f"LONGITUDINAL_CHANGE_BENCHMARK_SCORE={score:.4f}")
print(f"LONGITUDINAL_CHANGE_BENCHMARK_PASSED={passed}")
print(f"LONGITUDINAL_CHANGE_BENCHMARK_TOTAL={total}")
failed = [case for case in report.get("outcomes", []) if not case.get("pass")]
print("LONGITUDINAL_CHANGE_BENCHMARK_FAILED_CASES=" + json.dumps(failed, sort_keys=True))
if total == 0 or passed != total or score != 1.0 or failed:
    raise SystemExit("Longitudinal change benchmark gate failed.")
PY
