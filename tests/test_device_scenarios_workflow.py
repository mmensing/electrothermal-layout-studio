# SPDX-License-Identifier: MPL-2.0
"""Device scenarios and TLM extraction stay inside document/job safeguards."""
import pytest
from gui_test_helpers import _answer_dialog, _click, _fill, _idle
from gui_test_helpers import guided_window as guided_window
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLabel

pytestmark=pytest.mark.desktop


@pytest.mark.parametrize('identifier,title,expected',[
    ('E37','Voltage-controlled GaN coupon','voltage'),
    ('E38','Parallel GaN banks','voltage'),
    ('E39','SiC: heat in semiconductor (2 A)','current'),
])
def test_device_choice_uses_same_gui_command_and_editable_project(guided_window,qtbot,monkeypatch,identifier,title,expected):
    w=guided_window
    w.load_example(identifier)
    _idle(w,qtbot)
    monkeypatch.setattr(QInputDialog,'getItem',lambda *args:(title,True))
    w.guided.execute('file.example_scenario')
    _idle(w,qtbot)
    drains=[port for port in w.project.power.ports if port.id.startswith('drain')]
    assert drains and all(port.kind==expected for port in drains)
    assert w.result is None and w.mesh is None


def test_tlm_command_resolves_stay_before_selecting_output(guided_window,qtbot,monkeypatch):
    w=guided_window
    w.load_example('E27')
    _idle(w,qtbot)
    _click(w,w.guided.stage_buttons['layers'])
    _fill(w,'thickness','3.5')
    before=w.project.to_dict()
    monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *args:pytest.fail('Stay must cancel before opening the output chooser'))
    _answer_dialog('Stay')
    w.guided.execute('studies.contact')
    assert w.job is None and w.project.to_dict()==before and w.guided.editor.has_changes()
    _click(w,w.guided.editor.cancel_button)


def test_real_background_tlm_study_preserves_working_project_and_displays_fit(guided_window,qtbot,monkeypatch,tmp_path):
    w=guided_window
    w.load_example('E27')
    _idle(w,qtbot)
    before=w.project.to_dict()
    monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *args:str(tmp_path))
    w.guided.execute('studies.contact')
    assert w.job is not None
    assert not w.guided.registry.available('studies.contact')
    _idle(w,qtbot)
    assert w.project.to_dict()==before and w.result is None
    assert w.contact_study_dialog.isVisible()
    assert w.contact_study_report['fit']['Rs_ohm_per_square']==pytest.approx(10.,rel=.01)
    assert len(list(tmp_path.glob('tlm-study-*/gap-*/*.etl')))==4
    w.contact_study_dialog.close()


def test_configured_output_study_runs_stored_values_without_duplicating_specs(guided_window,qtbot,monkeypatch):
    w=guided_window
    w.load_example('E37','voltage_controlled')
    _idle(w,qtbot)
    before=w.project.to_dict()
    label='1. '+w.project.sweeps[0].name
    monkeypatch.setattr(QInputDialog,'getItem',lambda *args:(label,True))
    w.guided.execute('studies.configured')
    _idle(w,qtbot)
    assert w.project.to_dict()==before
    assert [row.value for row in w.studies]==[.05,.1,.2,.3]
    assert all(row.result.converged and not row.error for row in w.studies)
    assert w.study_panel.parameter=='power.ports.drain_a.value'


def test_fixture_dialog_uses_selected_real_result_without_editing_the_model(guided_window,qtbot):
    w=guided_window
    w.load_example('E40')
    _idle(w,qtbot)
    w.guided.execute('simulation.run')
    _idle(w,qtbot)
    before,result=w.project.to_dict(),w.result
    w.guided.execute('results.engineering')
    assert w.engineering_dialog.isVisible()
    assert w.project.to_dict()==before and w.result is result
    assert any('mΩ' in label.text() for label in w.engineering_dialog.findChildren(QLabel))
    w.engineering_dialog.close()
