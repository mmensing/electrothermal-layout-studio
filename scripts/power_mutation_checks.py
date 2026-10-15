# SPDX-License-Identifier: MPL-2.0
"""Run genuine targeted regressions against isolated deliberately broken copies."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

workspace = Path(__file__).resolve().parents[1]
root = workspace / '.artifacts' / 'power-routing' / 'mutation-checks'
root.mkdir(parents=True, exist_ok=True)
cases = [
    ('averaged_point_touch', 'visualization/sampling.py',
     'local = result.element_fields.get(layer, {}).get(name)',
     'local = np.asarray(result.fields[layer][name])[result.mesh.triangles]',
     'tests/test_power_local_fields.py::test_point_touch_local_probe_profile_archive_and_export'),
    ('infeasible_pareto', 'optimization.py',
     'row.get("status") in FEASIBLE and row.get("objectives")',
     'row.get("objectives")',
     'tests/test_power_optimization.py::test_seeded_grid_is_bounded_and_feasible_first_pareto'),
]
reports = []
for name, module, old, new, test in cases:
    source = root / name / 'src'
    shutil.copytree(workspace/'src', source, ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'), dirs_exist_ok=True)
    target = source / 'etlayout' / module
    text = target.read_text(encoding='utf-8')
    assert text.count(old) == 1
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    target.write_text(text.replace(old, new), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(source), QT_QPA_PLATFORM='offscreen', MPLBACKEND='Agg', PYTHONUTF8='1')
    origin = subprocess.check_output([sys.executable, '-c', 'import etlayout; print(etlayout.__file__)'], cwd=workspace, env=environment, text=True).strip()
    assert str(source).lower() in origin.lower()
    command = [sys.executable, '-m', 'pytest', test, '-q', '--basetemp', str(root / name / 'pytest-temp')]
    start = time.perf_counter()
    result = subprocess.run(command, cwd=workspace, env=environment, capture_output=True, text=True, encoding='utf-8')
    (root / name / 'pytest.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    record = {'mutation':name,'module':module,'test':test,'original_sha256':original_hash,'import_origin':origin,
              'expected_exit_code':1,'exit_code':result.returncode,'elapsed_seconds':time.perf_counter()-start,
              'assertion_failure': 'AssertionError' in result.stdout,'summary':result.stdout[-3500:]}
    reports.append(record)
    assert result.returncode == 1 and 'AssertionError' in result.stdout, record
    print(name + ': regression rejected deliberately broken code', flush=True)
environment['PYTHONPATH'] = str(workspace/'src')
command = [sys.executable,'-m','pytest',*(case[-1] for case in cases),'-q','--basetemp',str(root/'unmutated-temp')]
result = subprocess.run(command, cwd=workspace, env=environment,capture_output=True,text=True,encoding='utf-8')
(root/'unmutated.log').write_text(result.stdout+result.stderr,encoding='utf-8')
assert result.returncode == 0, result.stdout+result.stderr
(root/'summary.json').write_text(json.dumps({'cases':reports,'unmutated_exit_code':result.returncode,'unmutated_summary':result.stdout},indent=2),encoding='utf-8')
print(result.stdout,flush=True)
