# SPDX-License-Identifier: MPL-2.0
"""Versioned portable data contracts. All coordinates and properties use SI units.

Polygon lists are open rings (an optional repeated final vertex is accepted).
Runtime geometry uses Shapely; runtime fields use NumPy. Neither is serialized
with pickle. See io.project for the JSON/NPZ project archive.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

import numpy as np
from numpy.typing import NDArray

from etlayout import __version__

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Progress = Callable[[float, str], None]
Cancel = Callable[[], bool]


class SimulationError(RuntimeError):
    """A user-actionable setup or numerical failure."""


class Cancelled(SimulationError):
    pass


def checkpoint(cancel: Cancel | None = None, progress: Progress | None = None,
               fraction: float = 0., message: str = "") -> None:
    if cancel and cancel():
        raise Cancelled("Simulation cancelled")
    if progress:
        progress(fraction, message)


@dataclass
class Diagnostic:
    severity: str
    message: str
    object_id: str = ""
    bounds: list[float] | None = None


@dataclass
class PropertyModel:
    """constant, linear, polynomial (powers of T-Tref), or table."""
    kind: str = "constant"
    value: float = 1.
    reference_temperature: float = 300.
    alpha: float = 0.
    coefficients: list[float] = field(default_factory=list)
    temperatures: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    minimum_temperature: float = 200.
    maximum_temperature: float = 600.


@dataclass
class Material:
    id: str
    name: str
    category: str = "metal"
    resistivity: PropertyModel = field(default_factory=lambda: PropertyModel(value=1.7e-8))
    thermal_conductivity: PropertyModel = field(default_factory=lambda: PropertyModel(value=400.))
    density: float = 8960.
    specific_heat: float = 385.
    # Multipliers along x/y and optional through-plane property, in SI.
    electrical_anisotropy: list[float] = field(default_factory=lambda: [1., 1.])
    thermal_anisotropy: list[float] = field(default_factory=lambda: [1., 1.])
    thermal_conductivity_z: float | None = None
    notes: str = "Representative values; calibrate for the process."
    source: str = "User supplied"
    version: str = "1"
    reference: bool = False


@dataclass
class RegionSpec:
    """Mask Boolean and/or explicit SI polygons; line for edge terminals/cuts.

    sources are GDS layer/datatype keys, e.g. '1/0'. operation combines them
    left-to-right; exclude_sources are then subtracted as a union. Explicit
    polygons are unioned with that result. full_domain
    selects ROI (or physical domain extent). Empty spec resolves to empty, never all.
    """
    sources: list[str] = field(default_factory=list)
    operation: str = "union"
    polygons: list[dict[str, Any]] = field(default_factory=list)
    full_domain: bool = False
    line: list[list[float]] = field(default_factory=list)
    exclude_sources: list[str] = field(default_factory=list)


@dataclass
class ElectricalModel:
    """bulk, sheet, semiconductor, or hemt."""
    kind: str = "bulk"
    sheet_resistance: PropertyModel = field(default_factory=lambda: PropertyModel(value=400.))
    carrier_density: float = 1e23  # m^-3 (semiconductor)
    mobility: float = .1  # m²/(V s)
    mobility_exponent: float = 1.5
    reference_temperature: float = 300.
    gate_voltage: float = 0.
    threshold_voltage: float = -3.
    threshold_temperature_coefficient: float = 0.
    gate_coupling: float = 1e16  # sheet density per volt, m^-2 V^-1
    smoothing_voltage: float = .1
    maximum_sheet_density: float = 2e17
    background_sheet_density: float = 0.
    minimum_sheet_conductance: float = 1e-10
    gate_regions: list[str] = field(default_factory=list)  # gate terminal ids, empty = entire sheet


@dataclass
class PhysicalLayer:
    id: str
    name: str
    material_id: str
    thickness: float = 1e-6
    z: float = 0.
    role: str = "conductor"  # conductor, insulator, thermal
    geometry: RegionSpec = field(default_factory=RegionSpec)
    electrical: ElectricalModel = field(default_factory=ElectricalModel)
    visible: bool = True
    color: str = "#3F8F83"
    opacity: float = .75
    mesh_size: float | None = None
    net_id: str = ""
    loss_role: str = "auto"  # auto, routing_metal, other_metal, access, channel


@dataclass
class Interface:
    id: str
    layer_a: str
    layer_b: str
    geometry: RegionSpec = field(default_factory=RegionSpec)
    # none, ideal, specific (Ohm m²), lumped (Ohm), via (rho*height), conductance (S/m²)
    electrical_kind: str = "none"
    electrical_value: float = 1e-10
    material_id: str | None = None
    height: float = 1e-6
    # none, conductance (W/m²K), resistance (m²K/W), stack (half-thickness + gap)
    thermal_kind: str = "none"
    thermal_value: float = 1e6
    thermal_resistance_area: float = 0.
    enabled: bool = True
    model_kind: str = "effective"  # effective, physical_via, physical_contact
    height_mode: str = "explicit"  # explicit or stack (surface-to-surface separation)
    enclosure: float = 0.
    allow_partial: bool = False
    replaces_layers: list[str] = field(default_factory=list)
    resistance_semantics: str = "array"  # legacy lumped value is entire array; instance is per island
    purpose: str = "connection"  # connection or device_load
    reference_temperature: float = 300.
    temperature_coefficient: float = 0.


@dataclass
class Terminal:
    id: str
    layer_id: str
    geometry: RegionSpec = field(default_factory=RegionSpec)
    # voltage, ground, current (total A, equipotential), density (A/m²; edge area = length × thickness),
    # floating (equipotential, zero total current), insulated, gate
    kind: str = "voltage"
    value: float = 0.
    name: str = ""


@dataclass
class ThermalBoundary:
    id: str
    layer_id: str
    geometry: RegionSpec = field(default_factory=lambda: RegionSpec(full_domain=True))
    # temperature (K), coefficient (W/m²K), resistance_area (m²K/W), resistance (K/W), insulated
    kind: str = "coefficient"
    value: float = 1e6
    ambient: float = 300.


@dataclass
class HeatSource:
    id: str
    layer_id: str
    geometry: RegionSpec = field(default_factory=lambda: RegionSpec(full_domain=True))
    kind: str = "density"  # density W/m² or total W
    value: float = 0.


@dataclass
class RefinementRegion:
    id: str
    geometry: RegionSpec
    size: float
    axis_sizes: list[float] = field(default_factory=list)  # optional x/y spacing on conforming rectangular faces


@dataclass
class MeshSettings:
    max_size: float = 5e-6
    min_size: float = .25e-6
    contact_size: float = 1e-6
    corner_refinement: bool = True
    max_elements: int = 250000
    max_dofs: int = 500000
    refinements: list[RefinementRegion] = field(default_factory=list)


@dataclass
class SolverSettings:
    mode: str = "electrical"  # electrical, thermal, electrothermal
    initial_temperature: float = 300.
    max_iterations: int = 100
    relative_tolerance: float = 1e-6
    absolute_tolerance: float = 1e-6
    relaxation: float = .6
    linear_solver: str = "direct"  # direct or cg
    linear_tolerance: float = 1e-10
    current_balance_tolerance: float = 1e-3
    power_balance_tolerance: float = 5e-3
    thermal_balance_tolerance: float = 1e-2
    floating_policy: str = "error"  # error or reference (explicit per-component gauge)
    thermal_include_joule: bool = False  # thermal mode: compute electrical losses once at initial T
    resistance_terminals: list[str] = field(default_factory=list)  # voltage measurement pair; empty = unambiguous automatic pair
    resistance_current_terminal: str = ""  # current reference for Kelvin/multiport measurements


@dataclass
class LayoutSource:
    path: str = ""
    cell: str = ""
    sha256: str = ""
    dbu_um: float = .001
    # instance selection is backend-specific stable path returned by LayoutDocument.instances
    instance_path: list[int] = field(default_factory=list)


@dataclass
class SweepSpec:
    parameter: str = "terminals.drain.value"  # dotted path through dataclasses; list uses id
    values: list[Any] = field(default_factory=list)
    mode: str = "electrical"
    name: str = "Sweep"


@dataclass
class Waveform:
    """SI excitation: constant, pulse, or endpoint-held piecewise_linear.

    Pulse width is the high plateau duration; rise + width + fall <= period.
    A zero period requests a single pulse. Values have the target's SI unit.
    """
    kind: str = "constant"
    value: float = 0.
    low: float = 0.
    high: float = 1.
    delay: float = 0.
    width: float = 1e-4
    period: float = 2e-4
    rise_time: float = 0.
    fall_time: float = 0.
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)


@dataclass
class TransientDrive:
    target: str = "terminals.drain.value"
    waveform: Waveform = field(default_factory=Waveform)


@dataclass
class TransientSettings:
    enabled: bool = False
    stop_time: float = 1e-3
    time_step: float = 1e-5
    output_interval: float = 1e-5
    max_steps: int = 100000
    max_snapshots: int = 1000
    max_history_bytes: int = 256 * 1024 * 1024
    drives: list[TransientDrive] = field(default_factory=list)
    initialization: str = "uniform"  # uniform, steady, saved or resume
    initial_state_digest: str = ""  # identity of required external thermal state for saved/resume
    capacity_scheme: str = "consistent"  # consistent P1 or diagonal row-sum lumped thermal capacity
    retain_peak: bool = True
    periodic_tolerance: float = 0.  # maximum cycle-to-cycle nodal change, K; zero disables
    periodic_min_cycles: int = 3
    periodic_required_cycles: int = 2


@dataclass
class SupportSelector:
    """Union within each populated category; intersection between categories. SI geometry."""
    layers: list[str] = field(default_factory=list)
    materials: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    contacts: list[str] = field(default_factory=list)
    ports: list[str] = field(default_factory=list)
    geometry: RegionSpec | None = None


@dataclass
class MeasurementSpec:
    id: str
    quantity: str = "metal_current_density"
    selector: SupportSelector = field(default_factory=SupportSelector)
    aggregation: str = "maximum"  # integral, mean, rms, percentile, maximum, minimum, exceedance
    units: str = ""  # validated against quantity, empty selects canonical SI units
    weighting: str = "area"
    vector_component: str = "magnitude"  # magnitude, x/y for in-plane vectors, signed for contact normal
    percentile: float = 95.
    limit: float | None = None
    lower_limit: float | None = None
    averaging_length: float = 0.  # fixed square side; clipped to support and divided by supported area
    reference_boundary: str = ""
    reference_temperature: float | None = None
    effective_thickness: float | None = None  # explicit reporting-only sheet-current conversion
    threshold: float | None = None  # dimensional sample threshold, only for exceedance
    threshold_units: str = ""  # empty selects quantity's canonical SI unit
    max_exceedance_fraction: float | None = None  # independent dimensionless constraint in [0, 1]


@dataclass
class PortLanding:
    terminal_id: str  # physical footprint is the referenced Terminal.geometry, never an entire net
    resistance: float = 0.  # ohms, zero is an explicitly ideal landing
    thermal_fraction: float = 0.  # fraction of feed loss deposited in this physical layer


@dataclass
class PhysicalPort:
    id: str
    landings: list[PortLanding] = field(default_factory=list)
    kind: str = "voltage"  # voltage, ground, current, floating; positive current enters domain
    value: float = 0.
    external_node: str = ""  # empty uses this port ID; explicit aliases share one external node
    name: str = ""


@dataclass
class DeviceGroup:
    id: str
    selector: SupportSelector = field(default_factory=SupportSelector)
    active_width: float = 0.
    active_area: float = 0.
    source_port: str = ""
    drain_port: str = ""
    gate_terminal: str = ""
    name: str = ""


@dataclass
class RoutingRules:
    id: str = "rules"
    dbu: float = 1e-9  # meters; round nearest DBU, then check all rules
    allowed_layers: list[str] = field(default_factory=list)
    minimum_width: dict[str, float] = field(default_factory=dict)
    minimum_spacing: dict[str, float] = field(default_factory=dict)
    net_spacing: dict[str, float] = field(default_factory=dict)  # sorted net pair joined by ':'
    via_size: float = 1e-6
    via_pitch: float = 2e-6
    via_enclosure: float = .25e-6
    transitions: list[list[str]] = field(default_factory=list)
    minimum_notch: float = 0.
    minimum_bend_radius: float = 0.
    protected_masks: list[str] = field(default_factory=list)
    keepouts: list[RegionSpec] = field(default_factory=list)
    fixed_regions: list[RegionSpec] = field(default_factory=list)
    via_spacing: float = 0.  # appended to preserve positional construction; same-transition edge gap


@dataclass
class RoutingRecipe:
    id: str
    kind: str = "bus"  # bus, finger_feed, strap, via_array
    version: int = 1
    layer_id: str = ""
    mask: str = ""  # GDS layer/datatype key
    edit_region: RegionSpec = field(default_factory=RegionSpec)
    parameters: dict[str, Any] = field(default_factory=dict)  # declarative recipe-specific scalars/lists only
    source_hash: str = ""
    instance_path: list[int] = field(default_factory=list)
    all_instances: bool = False
    seed: int = 0
    net_id: str = ""
    interface_id: str = ""


@dataclass
class DesignVariable:
    id: str
    recipe_id: str
    parameter: str
    kind: str = "continuous"  # continuous, integer, choice
    minimum: float = 0.
    maximum: float = 1.
    values: list[Any] = field(default_factory=list)
    step: float = 0.


@dataclass
class OperatingScenario:
    id: str
    settings: dict[str, Any] = field(default_factory=dict)  # validated dotted project parameter paths
    service_port: str = ""
    reference_port: str = ""
    required_current: float = 0.
    service_mode: str = "target_current"  # target_current or fixed_voltage with minimum required current
    compliance_voltage: float = 100.
    service_tolerance: float = .01
    weight: float = 1.


@dataclass
class ObjectiveSpec:
    measurement_id: str
    direction: str = "min"
    scenario_aggregation: str = "worst"  # worst or weighted_mean


@dataclass
class OptimizationExperiment:
    id: str
    recipe_ids: list[str] = field(default_factory=list)
    variables: list[DesignVariable] = field(default_factory=list)
    scenarios: list[OperatingScenario] = field(default_factory=list)
    objectives: list[ObjectiveSpec] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)  # measurement IDs with explicit limits
    protected_devices: list[str] = field(default_factory=list)
    max_evaluations: int = 20
    seed: int = 0
    finalists: int = 3
    refinement_factors: list[float] = field(default_factory=lambda: [1., .5])
    maximum_mesh_change: float = .05
    improvement_margin: float = 0.
    assumptions: list[str] = field(default_factory=list)
    max_wall_seconds: float = 3600.


@dataclass
class PowerSettings:
    measurements: list[MeasurementSpec] = field(default_factory=list)
    ports: list[PhysicalPort] = field(default_factory=list)
    devices: list[DeviceGroup] = field(default_factory=list)
    rules: RoutingRules = field(default_factory=RoutingRules)
    recipes: list[RoutingRecipe] = field(default_factory=list)
    experiments: list[OptimizationExperiment] = field(default_factory=list)
    thermal_reference_boundary: str = ""
    thermal_reference_temperature: float | None = None


@dataclass
class TemperatureLimit:
    """Absolute maximum over authoritative thermal P1 fields; SI kelvin.

    An empty layer list covers every active thermal sheet. Optional geometry
    clips support exactly, including partial elements. Equality fails acceptance.
    """
    id: str = "maximum_temperature"
    enabled: bool = True
    maximum_temperature: float = 423.15
    layer_ids: list[str] = field(default_factory=list)
    geometry: RegionSpec | None = None
    evaluation: str = "fail_after"  # fail_after or stop_transient


@dataclass
class Project:
    name: str = "Untitled"
    schema_version: int = 2
    layout: LayoutSource = field(default_factory=LayoutSource)
    # ROI in SI; empty uses imported bbox or explicit layer bounds.
    roi: RegionSpec | None = None
    display_roi: RegionSpec | None = None
    acknowledge_clipped_boundaries: bool = False
    layers: list[PhysicalLayer] = field(default_factory=list)
    materials: dict[str, Material] = field(default_factory=dict)
    interfaces: list[Interface] = field(default_factory=list)
    terminals: list[Terminal] = field(default_factory=list)
    thermal_boundaries: list[ThermalBoundary] = field(default_factory=list)
    heat_sources: list[HeatSource] = field(default_factory=list)
    mesh: MeshSettings = field(default_factory=MeshSettings)
    solver: SolverSettings = field(default_factory=SolverSettings)
    transient: TransientSettings = field(default_factory=TransientSettings)
    sweeps: list[SweepSpec] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=dict)
    notes: str = ""
    power: PowerSettings = field(default_factory=PowerSettings)
    temperature_limits: list[TemperatureLimit] = field(default_factory=list)

    def layer(self, layer_id: str) -> PhysicalLayer:
        return next(x for x in self.layers if x.id == layer_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Preserve pre-limit full configuration identities and limit-free
        # archives for strict older readers. Explicit disabled records persist.
        if not data["temperature_limits"]:
            data.pop("temperature_limits")
        return data


@dataclass
class PreparedGeometry:
    domain: Any  # shapely Polygon/MultiPolygon
    layers: dict[str, Any]
    interfaces: dict[str, Any] = field(default_factory=dict)
    terminals: dict[str, Any] = field(default_factory=dict)
    thermal_boundaries: dict[str, Any] = field(default_factory=dict)
    heat_sources: dict[str, Any] = field(default_factory=dict)
    refinements: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class PlanarMesh:
    points: FloatArray  # (N,2), meters
    triangles: IntArray  # (M,3)
    # prefixes layer:, interface:, terminal:, thermal:, heat:; values global triangle indices
    regions: dict[str, IntArray]
    terminal_nodes: dict[str, IntArray] = field(default_factory=dict)
    boundary_nodes: dict[str, IntArray] = field(default_factory=dict)
    statistics: dict[str, Any] = field(default_factory=dict)
    boundary_edges: dict[str, IntArray] = field(default_factory=dict)

    def elements(self, layer_id: str) -> IntArray:
        return self.regions.get('layer:' + layer_id, np.array([], dtype=np.int64))

    @property
    def centroids(self) -> FloatArray:
        return self.points[self.triangles].mean(axis=1)

    @property
    def areas(self) -> FloatArray:
        p = self.points[self.triangles]
        a, b = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        return np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) / 2


@dataclass
class SimulationResult:
    mesh: PlanarMesh
    # All nodal scalar fields have length N; all element scalar/vector fields length M.
    # Inactive entries are NaN. Names: potential, temperature (nodes), electric_field,
    # sheet_current, current_density (M,2), joule_density (W/m²), thermal_gradient (M,2).
    fields: dict[str, dict[str, FloatArray]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    terminals: dict[str, dict[str, float]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    iterations: list[dict[str, float]] = field(default_factory=list)
    converged: bool = False
    configuration: dict[str, Any] = field(default_factory=dict)
    configuration_hash: str = ""
    elapsed_seconds: float = 0.
    solver_version: str = __version__
    physics_hash: str = ""  # excludes presentation/annotations; derived for legacy archives
    transient: TransientHistory | None = None
    execution: dict[str, Any] = field(default_factory=dict)  # machine provenance, excluded from physical identity
    element_fields: dict[str, dict[str, FloatArray]] = field(default_factory=dict)  # authoritative P1 (M,3)
    interface_fields: dict[str, dict[str, FloatArray]] = field(default_factory=dict)  # contact P1 (M,3)/element (M,)
    analysis: dict[str, Any] = field(default_factory=dict)  # versioned scoped power-analysis records
    field_version: int = 2


@dataclass
class TransientHistory:
    """Saved fields share one mesh; rows include every accepted integration step.

    ``times`` aligns with ``snapshots``. Snapshots never contain nested histories.
    At step endpoints electrical fields and source values use the incoming
    (left-limit) drive, avoiding a spurious full-step pulse at ideal edges.
    """
    times: FloatArray = field(default_factory=lambda: np.array([], dtype=float))
    snapshots: list[SimulationResult] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    completed: bool = False


class MeshBackend(Protocol):
    def generate(self, project: Project, geometry: PreparedGeometry,
                 progress: Progress | None = None, cancel: Cancel | None = None) -> PlanarMesh: ...


class SimulationBackend(Protocol):
    def solve(self, project: Project, mesh: PlanarMesh,
              progress: Progress | None = None, cancel: Cancel | None = None) -> SimulationResult: ...
