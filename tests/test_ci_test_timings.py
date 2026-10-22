# SPDX-License-Identifier: MPL-2.0
"""CI timing evidence preserves test outcomes and identifies duplicated work."""
import json
import runpy
from pathlib import Path

import pytest

TIMING = runpy.run_path(str(Path(__file__).parents[1] / "scripts/summarize_test_timings.py"))


def test_junit_summary_measures_fixture_inclusive_costs_and_keeps_duplicate_executions(tmp_path):
    first, second = tmp_path / "fast.xml", tmp_path / "compact.xml"
    first.write_text('<testsuites><testsuite time="10"><testcase classname="a" name="shared" time="2"/>'
                     '<testcase classname="b" name="bad" time="3"><failure/></testcase>'
                     '<testcase classname="b" name="skipped" time="0"><skipped/></testcase></testsuite></testsuites>')
    second.write_text('<testsuite time="4"><testcase classname="a" name="shared" time="1"/>'
                      '<testcase classname="c" name="error" time="2"><error/></testcase></testsuite>')
    report = TIMING["summarize"]({"fast": first, "compact": second, "absent": tmp_path / "missing.xml"}, limit=1)
    fast = report["reports"]["fast"]
    assert fast["cases"] == 3 and fast["suite_seconds"] == 10 and fast["case_seconds"] == 5
    assert fast["outcomes"] == {"passed": 1, "failure": 1, "error": 0, "skipped": 1}
    assert fast["slow_tests"][0]["id"] == "b::bad"
    assert report["reports"]["compact"]["suite_seconds"] == 4
    assert report["reports"]["compact"]["outcomes"]["error"] == 1
    assert report["slow_modules"] == [{"module": "a", "cases": 2, "seconds": 3}]
    assert report["repeated_tests_across_reports"] == {
        "count": 1, "examples": [{"id": "a::shared", "reports": ["compact", "fast"], "executions": 2}]}
    assert report["missing_reports"] == ["absent"] and not report["case_times_are_pipeline_wall_time"]


@pytest.mark.parametrize("content", [
    '<testsuite><testcase time="nan"/></testsuite>',
    '<testsuite><testcase time="-1"/></testsuite>',
    '<!DOCTYPE testsuite [<!ENTITY a "expanded">]><testsuite/>',
    '<not-junit/>',
])
def test_invalid_duration_and_non_junit_inputs_cannot_be_reported_as_test_evidence(tmp_path, content):
    source = tmp_path / "input.xml"
    source.write_text(content)
    with pytest.raises(ValueError):
        TIMING["read_report"](source)


def test_summary_cli_records_missing_evidence_and_escapes_markdown(tmp_path, monkeypatch):
    output, summary = tmp_path / "timings.json", tmp_path / "summary.md"
    source = tmp_path / "input.xml"
    source.write_text('<testsuite time="1"><testcase classname="a|&lt;b&gt;" name="case" time="1"/></testsuite>')
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert TIMING["main"](["--report", "actual=" + str(source), "--report", "missing=" + str(tmp_path / "absent.xml"),
                           "--output", str(output), "--github-summary"]) == 0
    assert json.loads(output.read_text())["missing_reports"] == ["missing"]
    assert "a&#124;&lt;b>" in summary.read_text() and "Missing report evidence: missing" in summary.read_text()
    with pytest.raises(SystemExit) as duplicate:
        TIMING["main"](["--report", "same=" + str(source), "--report", "same=" + str(source), "--output", str(output)])
    assert duplicate.value.code == 2


def test_timing_input_is_bounded_before_xml_parsing(tmp_path, monkeypatch):
    source = tmp_path / "too-large.xml"
    source.write_bytes(b"x" * 33)
    monkeypatch.setitem(TIMING["read_report"].__globals__, "MAX_REPORT_BYTES", 32)
    with pytest.raises(ValueError, match="summary budget"):
        TIMING["read_report"](source)
