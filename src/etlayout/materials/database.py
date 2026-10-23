# SPDX-License-Identifier: MPL-2.0
"""SQLite user materials, immutable reference seeds, and portable JSON export."""
from __future__ import annotations

import csv
import json
import sqlite3
import uuid
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from platformdirs import user_data_path

from etlayout.models import Material, PropertyModel, SimulationError

from .models import validate_property


def reference_materials() -> dict[str, Material]:
    # Nominal room-temperature engineering values, not metrology or process calibration.
    metals = [
        ("copper", "Copper", 1.68e-8, 401., 8960., 385., .00393),
        ("aluminum", "Aluminum", 2.65e-8, 237., 2700., 897., .00429),
        ("gold", "Gold", 2.44e-8, 318., 19300., 129., .0034),
        ("silver", "Silver", 1.59e-8, 429., 10490., 235., .0038),
        ("titanium", "Titanium", 4.2e-7, 21.9, 4500., 523., .0038),
        ("nickel", "Nickel", 6.99e-8, 90.9, 8908., 444., .006),
        ("platinum", "Platinum", 1.06e-7, 71.6, 21450., 133., .00392),
        ("tungsten", "Tungsten", 5.6e-8, 173., 19300., 134., .0045),
        ("tin", "TiN (representative film)", 1e-6, 20., 5220., 550., .001),
        ("tan", "TaN (representative film)", 2e-6, 10., 14300., 300., .0002),
    ]
    result = {}
    for key, name, rho, k, density, cp, alpha in metals:
        film = key in {"tin", "tan"}
        result[key] = Material(
            key, name, "film" if film else "metal",
            PropertyModel("linear", rho, alpha=alpha, minimum_temperature=250., maximum_temperature=500.),
            PropertyModel(value=k, minimum_temperature=250., maximum_temperature=500.), density, cp,
            source=("Illustrative thin-film seed; replace using measured process data" if film else
                    "Nominal bulk engineering seed; CRC Handbook of Chemistry and Physics, electrical "
                    "resistivity and thermal conductivity tables (rounded; no certified accuracy)"),
            notes="300 K nominal. Grain size, impurities, film thickness and deposition can change transport. "
                  "Constant thermal conductivity is a screening assumption.", reference=True)
    others = [
        ("silicon", "Silicon", "semiconductor", 1., 148., 2329., 700.),
        ("sic", "4H-SiC", "semiconductor", 1., 370., 3210., 690.),
        ("gan", "GaN", "semiconductor", 1., 130., 6150., 490.),
        ("2deg", "GaN 2DEG", "2deg", 1., 130., 6150., 490.),
        ("sio2", "SiO₂", "dielectric", 1e16, 1.4, 2200., 703.),
        ("sin", "SiN", "dielectric", 1e14, 3., 3100., 700.),
        ("alumina", "Al₂O₃", "dielectric", 1e14, 30., 3950., 880.),
        ("aln", "AlN (synthetic thermal seed)", "dielectric", 1e14, 100., 3260., 740.),
        ("polyimide", "Polyimide (synthetic thermal seed)", "dielectric", 1e14, .2, 1400., 1100.),
        ("substrate", "Custom substrate", "dielectric", 1e14, 10., 2500., 700.),
    ]
    for key, name, category, rho, k, density, cp in others:
        result[key] = Material(
            key, name, category, PropertyModel(value=rho), PropertyModel(value=k), density, cp,
            source=("Representative 300 K engineering seed. Semiconductor thermal reference context: "
                    "https://www.ioffe.ru/SVA/NSM/Semicond/ ; film dielectric values are process-dependent."),
            notes=("Electrical bulk resistivity is an illustrative effective parameter, not an intrinsic "
                   "semiconductor prediction. Select carrier/mobility, calibrated sheet resistance or HEMT model. "
                   "Thermal values are nominal isotropic approximations; calibrate for material quality." if
                   category in {"semiconductor", "2deg"} else
                   "Representative dielectric/substrate seed, not process-calibrated. SiN film k varies strongly."),
            reference=True)
    for key in ("aln", "polyimide"):
        result[key].source = "Synthetic sensitivity-study assumption; not measured process data or a certified product value."
        result[key].notes = ("Uncalibrated 300 K constant isotropic seed. Replace k_parallel, k_z, density and heat capacity "
                             "with measured film/package data and temperature validity bounds. No semiconductor electrostatics.")
    return result


def material_from_dict(data: dict) -> Material:
    copy = dict(data)
    for key in ("resistivity", "thermal_conductivity", "permittivity", "permeability"):
        if isinstance(copy.get(key), dict):
            copy[key] = PropertyModel(**copy[key])
    return Material(**copy)


def validate_material(material: Material) -> None:
    if not material.id.strip() or not material.name.strip():
        raise SimulationError("Material ID and name must be nonempty")
    validate_property(material.resistivity)
    validate_property(material.thermal_conductivity)
    for property_name in ("permittivity", "permeability"):
        property_model = getattr(material, property_name)
        if property_model is not None:
            validate_property(property_model)
    if material.density <= 0 or material.specific_heat <= 0:
        raise SimulationError("Density and specific heat must be positive")
    for a in (material.electrical_anisotropy, material.thermal_anisotropy):
        if len(a) != 2 or any(x <= 0 for x in a):
            raise SimulationError("Anisotropy requires positive x and y multipliers")
    if material.thermal_conductivity_z is not None and material.thermal_conductivity_z <= 0:
        raise SimulationError("Through-plane thermal conductivity must be positive")


class MaterialDatabase:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else user_data_path("etlayout", "etlayout") / "materials.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._reference = reference_materials()

    def _connect(self):
        return sqlite3.connect(self.path)

    def list_materials(self) -> list[Material]:
        with self._connect() as connection:
            user = [material_from_dict(json.loads(x[0])) for x in
                    connection.execute("SELECT payload FROM materials ORDER BY id")]
        return list(deepcopy(self._reference).values()) + user

    def get(self, material_id: str) -> Material:
        if material_id in self._reference:
            return deepcopy(self._reference[material_id])
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM materials WHERE id=?", (material_id,)).fetchone()
        if row is None:
            raise KeyError(material_id)
        return material_from_dict(json.loads(row[0]))

    def save(self, material: Material) -> None:
        if material.reference or material.id in self._reference:
            raise SimulationError("Reference materials are immutable. Duplicate the material before editing.")
        validate_material(material)
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO materials VALUES (?,?)",
                               (material.id, json.dumps(asdict(material), allow_nan=False)))

    def duplicate(self, material_id: str, name: str | None = None) -> Material:
        material = self.get(material_id)
        material.id = f"user-{uuid.uuid4().hex[:12]}"
        material.name = name or material.name + " (custom)"
        material.reference = False
        material.source = f"Derived from {material_id} version {material.version}; " + material.source
        self.save(material)
        return material

    def delete(self, material_id: str) -> None:
        if material_id in self._reference:
            raise SimulationError("Reference material cannot be deleted")
        with self._connect() as connection:
            connection.execute("DELETE FROM materials WHERE id=?", (material_id,))

    def restore_defaults(self) -> None:
        """Reload immutable defaults; custom records are deliberately preserved."""
        self._reference = reference_materials()

    def export_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps([asdict(m) for m in self.list_materials()], indent=2), encoding="utf-8")

    def import_json(self, path: str | Path) -> list[Material]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        materials = [material_from_dict(x) for x in data]
        for material in materials:
            material.id = f"user-{uuid.uuid4().hex[:12]}"
            material.reference = False
            validate_material(material)
        for material in materials:
            self.save(material)
        return materials

    @staticmethod
    def import_property_csv(path: str | Path) -> PropertyModel:
        """CSV columns temperature_K,value; property values in the owning SI unit."""
        with Path(path).open(newline="", encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        try:
            temperatures = [float(x["temperature_K"]) for x in rows]
            values = [float(x["value"]) for x in rows]
        except (KeyError, ValueError) as exc:
            raise SimulationError("CSV needs numeric columns temperature_K,value") from exc
        if not temperatures:
            raise SimulationError("Material CSV is empty")
        prop = PropertyModel("table", values[0], temperatures=temperatures, values=values,
                             minimum_temperature=min(temperatures), maximum_temperature=max(temperatures))
        validate_property(prop)
        return prop
