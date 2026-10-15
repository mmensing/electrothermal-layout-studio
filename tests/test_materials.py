# SPDX-License-Identifier: MPL-2.0
import numpy as np
import pytest

from etlayout.materials.database import MaterialDatabase, reference_materials
from etlayout.materials.models import evaluate_property
from etlayout.models import PropertyModel, SimulationError


def test_all_reference_materials_have_positive_properties():
    materials = reference_materials()
    assert len(materials) == 18
    for material in materials.values():
        assert evaluate_property(material.resistivity, 300.) > 0
        assert evaluate_property(material.thermal_conductivity, 300.) > 0
        assert material.source and material.notes and material.reference


def test_linear_polynomial_and_table():
    linear = PropertyModel("linear", 2., alpha=.01)
    assert evaluate_property(linear, 350.) == pytest.approx(3.)
    polynomial = PropertyModel("polynomial", 2., coefficients=[2., .1, .01])
    assert evaluate_property(polynomial, 310.) == pytest.approx(4.)
    table = PropertyModel("table", 1., temperatures=[300., 400.], values=[2., 4.])
    np.testing.assert_allclose(evaluate_property(table, [300, 350, 400]), [2, 3, 4])
    with pytest.warns(RuntimeWarning, match="endpoint"):
        assert evaluate_property(table, 450) == 4


def test_material_bounds_and_invalid_laws():
    with pytest.warns(RuntimeWarning, match="validity"):
        evaluate_property(PropertyModel(), 700)
    with pytest.raises(SimulationError, match="nonpositive"):
        evaluate_property(PropertyModel("linear", 1., alpha=-.1), 350)
    with pytest.raises(SimulationError, match="increasing"):
        evaluate_property(PropertyModel("table", temperatures=[300, 300], values=[1, 2]), 300)


def test_immutable_reference_and_persistent_customization(tmp_path):
    path = tmp_path / "materials.sqlite"
    db = MaterialDatabase(path)
    copper = db.get("copper")
    with pytest.raises(SimulationError, match="Duplicate"):
        db.save(copper)
    custom = db.duplicate("copper", "Process Cu")
    custom.resistivity.value *= 2
    db.save(custom)
    assert MaterialDatabase(path).get(custom.id).resistivity.value == pytest.approx(3.36e-8)
    assert db.get("copper").resistivity.value == pytest.approx(1.68e-8)
    db.restore_defaults()
    assert db.get(custom.id).name == "Process Cu"
    exported = tmp_path / "materials.json"
    db.export_json(exported)
    assert len(db.import_json(exported)) == 19
    db.delete(custom.id)
    with pytest.raises(KeyError):
        db.get(custom.id)


def test_csv_property_import(tmp_path):
    source = tmp_path / "measured.csv"
    source.write_text("temperature_K,value\n300,1.6e-8\n400,2.2e-8\n")
    prop = MaterialDatabase.import_property_csv(source)
    assert float(evaluate_property(prop, 350)) == pytest.approx(1.9e-8)
