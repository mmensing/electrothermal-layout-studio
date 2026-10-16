# SPDX-License-Identifier: MPL-2.0
"""Explicit CI groups; the unfiltered suite always retains every test."""
import pytest

NUMERICAL_MODULES = frozenset({
    'test_advanced_devices.py', 'test_advanced_interconnects.py', 'test_audit_regressions.py',
    'test_coupled.py', 'test_electrical.py', 'test_example_gallery.py', 'test_execution.py',
    'test_geometry_audit_fixes.py', 'test_hemt.py', 'test_measurement_support.py', 'test_meshing.py',
    'test_planar_references.py', 'test_power_local_fields.py', 'test_power_measurements.py',
    'test_power_optimization.py', 'test_power_optimization_failures.py', 'test_power_optimization_policy.py',
    'test_power_optimization_ranking.py', 'test_power_routing.py', 'test_power_routing_examples.py',
    'test_power_solver.py', 'test_power_solver_multiport.py', 'test_power_stack.py',
    'test_power_validation_components.py', 'test_retained_field_reanalysis.py', 'test_solver_examples.py',
    'test_study_execution.py', 'test_technology_templates.py', 'test_thermal.py', 'test_transient.py',
    'test_transient_examples.py', 'test_transient_export_context.py', 'test_transient_io.py',
    'test_transient_monotonicity.py', 'test_transient_multilayer.py', 'test_transient_studies.py',
    'test_transient_workflows.py', 'test_workflows.py',
})


def pytest_collection_modifyitems(items):
    for item in items:
        if any(item.get_closest_marker(name) for name in ('fast', 'numerical', 'desktop', 'packaging')):
            continue
        if {'qtbot', 'qapp'} & set(item.fixturenames):
            category = 'desktop'
        elif item.path.name in {'test_distribution_content.py', 'test_release_metadata.py'}:
            category = 'packaging'
        else:
            category = 'numerical' if item.path.name in NUMERICAL_MODULES else 'fast'
        item.add_marker(getattr(pytest.mark, category))


@pytest.fixture(autouse=True)
def isolated_desktop_settings(request, monkeypatch):
    """Only Qt tests replace the window's native preference-store constructor."""
    if not {"qtbot", "qapp"}.intersection(request.fixturenames):
        return
    from gui_test_helpers import isolate_window_settings

    isolate_window_settings(monkeypatch, request.getfixturevalue("tmp_path"))


@pytest.fixture(scope="session")
def expanded_nominal_factory(tmp_path_factory):
    """Solve configured extended references once; callers receive isolated copies."""
    from copy import deepcopy

    from etlayout.example_catalog import example_info
    from etlayout.examples import EXTENDED_EXAMPLES, make_example
    from etlayout.simulation.runner import run_project

    solved = {}

    def nominal(identifier):
        name = example_info(identifier).name
        if name not in EXTENDED_EXAMPLES:
            raise ValueError("This fixture only caches extended reference projects")
        if name not in solved:
            project = make_example(name, tmp_path_factory.mktemp(example_info(name).id))
            before = project.to_dict()
            result = run_project(project)
            assert project.to_dict() == before, "Nominal validation must not replace configured inputs"
            assert result.converged, (name, result.diagnostics)
            solved[name] = project, result
        return deepcopy(solved[name])

    return nominal
