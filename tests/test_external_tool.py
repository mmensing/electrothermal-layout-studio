# SPDX-License-Identifier: MPL-2.0
"""Real process resource enforcement added for optional field engines."""
import os
import sys
from pathlib import Path

import pytest

from etlayout.models import SimulationError
from etlayout.power.external_tool import run_external_tool


@pytest.mark.parametrize("value", [True, 0, 1024, 17*1024**3, 32.5*1024**2])
def test_invalid_memory_budget_does_not_start_a_process(tmp_path, value):
    with pytest.raises(SimulationError, match="memory budget"):
        run_external_tool(Path(sys.executable), ["-c", "raise AssertionError('must not run')"],
                          tmp_path, memory_limit_bytes=value)


def test_owned_process_memory_cap_is_recorded_and_enforced(tmp_path):
    program = "import sys,time\ntry:\n x=bytearray(256*1024**2)\n time.sleep(.3)\nexcept MemoryError:\n sys.exit(13)\n"
    report = run_external_tool(Path(sys.executable), ["-I", "-c", program], tmp_path,
                               timeout=10., memory_limit_bytes=64*1024**2)
    assert report["status"] in {"execution_failed", "memory_limit"}
    assert report["returncode"] != 0
    assert report["memory_limit_bytes"] == 64*1024**2
    assert report["seconds"] < 10
    if os.name == "nt":
        assert report["peak_job_commit_bytes"] > 0
        assert "Windows job" in report["memory_limit_policy"]
