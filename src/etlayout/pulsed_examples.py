# SPDX-License-Identifier: MPL-2.0
"""Reproducible heat-capacity and pulsed electrothermal teaching examples."""
from __future__ import annotations

from etlayout.models import (
    HeatSource,
    Interface,
    PhysicalLayer,
    RegionSpec,
    SweepSpec,
    ThermalBoundary,
    TransientDrive,
    TransientSettings,
    Waveform,
)


def build_pulsed_example(name, builder, project):
    from etlayout.examples import _metal, _terminal, edge

    if name.startswith("E17_"):
        builder.rect(1, 0, 0, 100, 100)
        project.layers = [_metal("plate", "1/0", thickness=10e-6, name="Copper thermal plate")]
        project.terminals = [_terminal("source", "plate", edge(0, 0, 0, 100), "ground"),
                             _terminal("drain", "plate", edge(100, 0, 100, 100), "voltage", 0.)]
        project.heat_sources = [HeatSource("heater", "plate", value=1e6)]
        project.thermal_boundaries = [ThermalBoundary("ambient", "plate", value=1e5, ambient=300.)]
        project.solver.mode = "thermal"
        project.mesh.max_size = 20e-6
        project.mesh.contact_size = 10e-6
        project.transient = TransientSettings(enabled=True, stop_time=2.1e-3, time_step=25e-6,
            output_interval=50e-6, drives=[TransientDrive("heat_sources.heater.value",
                Waveform(kind="pulse", low=0., high=1e6, delay=100e-6, width=200e-6, period=700e-6))])
        project.sweeps = [SweepSpec("transient.drives.0.waveform.high", [2.5e5, 5e5, 1e6], "thermal", "Pulse heat level")]
        project.notes = (
            "Uniform prescribed heat pulses in a 100 × 100 × 10 µm copper plate, with h=1e5 W/(m² K). "
            "Thermal-only operation excludes Joule heating. The spatially uniform limit has tau=rho*cp*t/h "
            "and high-level steady rise=10 K, so the transient can be checked against a lumped RC. "
            "Pulse width is the 200 µs plateau, period 700 µs, and delay 100 µs. Reduce dt to check time accuracy.")
    elif name.startswith("E18_"):
        builder.rect(1, 0, 0, 100, 10)
        project.layers = [_metal()]
        project.terminals = [_terminal("source", "metal", edge(0, 0, 0, 10), "ground"),
                             _terminal("drain", "metal", edge(100, 0, 100, 10), "current", .25)]
        project.thermal_boundaries = [ThermalBoundary("ambient", "metal", value=2e5, ambient=300.)]
        project.solver.mode = "electrothermal"
        project.mesh.max_size = 10e-6
        project.transient = TransientSettings(enabled=True, stop_time=400e-6, time_step=5e-6,
            output_interval=10e-6, drives=[TransientDrive("terminals.drain.value",
                Waveform(kind="pulse", low=0., high=.25, delay=25e-6, rise_time=10e-6,
                         width=60e-6, fall_time=10e-6, period=200e-6))])
        project.sweeps = [SweepSpec("transient.drives.0.waveform.high", [.05, .15, .25], "electrothermal", "Pulse current level")]
        project.notes = (
            "Pulsed 100 × 10 × 2 µm copper conductor with quasi-static current and temperature-dependent "
            "resistivity. The 0.25 A pulse has 10 µs rise, 60 µs high plateau and 10 µs fall, repeated every "
            "200 µs after a 25 µs delay. Finite edge times need time-step refinement. Heat capacity and "
            "cooling produce delayed thermal response; electrical capacitance and inductance are not included.")
    elif name.startswith("E19_"):
        # Two distinct metal islands exchange heat only through a shared sheet.
        # The unpowered receiver makes delayed thermal crosstalk unambiguous.
        builder.rect(1, 0, 0, 30, 30)
        builder.rect(2, 50, 0, 80, 30)
        builder.rect(3, -20, -20, 100, 50)
        project.layers = [
            PhysicalLayer("heater", "Pulsed copper heater", "copper", 5e-6, 0., "thermal",
                          RegionSpec(sources=["1/0"])),
            PhysicalLayer("receiver", "Unpowered copper receiver", "copper", 10e-6, 0., "thermal",
                          RegionSpec(sources=["2/0"]), color="#AEB7B3"),
            PhysicalLayer("substrate", "Shared silicon substrate", "silicon", 20e-6, -20e-6, "thermal",
                          RegionSpec(sources=["3/0"]), opacity=.3),
        ]
        project.terminals = []
        project.interfaces = [Interface("heater_substrate", "heater", "substrate",
            RegionSpec(sources=["1/0"]), thermal_kind="conductance", thermal_value=2e5),
            Interface("receiver_substrate", "receiver", "substrate",
            RegionSpec(sources=["2/0"]), thermal_kind="conductance", thermal_value=1e5)]
        project.heat_sources = [HeatSource("pulse_heat", "heater", RegionSpec(sources=["1/0"]), kind="total", value=.01)]
        project.thermal_boundaries = [ThermalBoundary("backside", "substrate", value=5e4, ambient=300.)]
        project.solver.mode = "thermal"
        project.mesh.max_size = 10e-6
        project.mesh.contact_size = 5e-6
        project.transient = TransientSettings(enabled=True, stop_time=1e-3, time_step=10e-6,
            output_interval=10e-6, drives=[TransientDrive("heat_sources.pulse_heat.value",
                Waveform(kind="pulse", low=0., high=.01, delay=50e-6, width=100e-6, period=0.))])
        project.sweeps = [SweepSpec("transient.drives.0.waveform.high", [.0025, .005, .01], "thermal",
                                  "Pulse heat level (W)")]
        project.notes = (
            "A 10 mW, 100 µs single pulse heats a 30 × 30 × 5 µm copper island. An unpowered "
            "30 × 30 × 10 µm copper receiver sits 20 µm away on the same 20 µm silicon substrate. "
            "Finite interface conductances create separate layer temperatures; receiver heating and "
            "its delayed peak arise entirely from thermal crosstalk. Inspect heater, substrate and "
            "receiver histories. Prescribed heat only; no electrical solve. Input energy is 1 µJ. "
            "Thickness-averaged sheets and representative materials require process calibration.")
    else:
        raise ValueError(f"Unknown pulsed example {name}")
