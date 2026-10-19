# SPDX-License-Identifier: MPL-2.0
"""Explicit device teaching configurations and fixture-deembedded observations."""
from __future__ import annotations

from etlayout.models import SimulationError


def device_scenarios(identifier):
    from etlayout.example_scenarios import ExampleScenario
    if identifier == 'E37':
        studies = (
            ('power.ports.drain_a.value', (.05, .1, .2, .3), 'Low-field voltage output; fixed 3 V gate'),
            ('terminals.gate_a.value', (-2., -1., 0., 1., 3.), 'Voltage-biased transfer; no deep-off leakage claim'),
            ('interfaces.source_contact_a.electrical_value', (3e-11, 1e-10, 3e-10), 'Finite source-contact resistivity sensitivity (ohm m2)'),
        )
        return (ExampleScenario('voltage_controlled', 'Voltage-controlled GaN coupon',
            'Use a finite 0.2 V drain reservoir and 3 V gate; output, transfer and contact sweeps retain the analytical low-field local-bias law.',
            (('power.ports.drain_a.kind', 'voltage'), ('power.ports.drain_a.value', .2)), studies=studies),)
    if identifier == 'E38':
        common = (('power.ports.drain_a.kind', 'voltage'), ('power.ports.drain_b.kind', 'voltage'),
            ('power.ports.drain_a.external_node', 'shared_drain'), ('power.ports.drain_b.external_node', 'shared_drain'),
            ('power.ports.drain_a.value', .25), ('power.ports.drain_b.value', .25))
        studies = (('terminals.gate_b.value', (1., 2., 3.), 'Parallel bank B gate mismatch; shared 0.25 V reservoir'),
            ('interfaces.source_contact_b.electrical_value', (1e-10, 3e-10, 5e-10), 'Parallel bank B source-contact mismatch'),
            ('thermal_boundaries.backside.value', (2e-6 + 150e-6/(2*370.), 5e-6 + 150e-6/(2*370.)), 'Common substrate cooling; voltage-controlled service may change'))
        choices = []
        for key, title, description, extra in (
            ('parallel_voltage', 'Parallel GaN banks', 'Bank currents emerge from one shared drain reservoir and the existing shared source; no bank current is prescribed.', ()),
            ('unequal_contact', 'Parallel banks: unequal contact', 'Bank B source contact has five times the baseline specific resistance; active geometry and shared reservoirs stay fixed.', (('interfaces.source_contact_b.electrical_value', 5e-10),)),
            ('gate_mismatch', 'Parallel banks: gate mismatch', 'Bank B gate is 1 V and bank A gate is 3 V; unequal bank/finger currents emerge from the local gate-bias law.', (('terminals.gate_b.value', 1.),)),
            ('source_asymmetry', 'Parallel banks: asymmetric source feed', 'Move only the localized source footprint toward bank A; retain both banks, source metal and thermal environment.', ()),
        ):
            choices.append(ExampleScenario(key, title, description, common + extra, studies=studies))
        return tuple(choices)
    if identifier == 'E39':
        studies = (('power.ports.drain.value', (1., 2., 3.), 'SiC on-state current; area and Ron,sp remain fixed'),
            ('layers.collector.thickness', (30e-6, 40e-6), 'Equal-current source-collection sensitivity'))
        return (
            ExampleScenario('continuous_reference', 'Lower-current SiC reference (2 A)',
                'Use 2 A over 0.4096 mm2 active area (488.28125 A/cm2). This is a synthetic lower-current reference, not a continuous rating.',
                (('power.ports.drain.value', 2.),), maximum_temperature_K=423.15, studies=studies),
            ExampleScenario('semiconductor_heat', 'SiC: heat in semiconductor (2 A)',
                'Retain 2 A, active area, Ron,sp and cooling; deposit the distributed device loss in its fully supported SiC thermal sheet rather than the legacy 50/50 metal-endpoint partition.',
                (('power.ports.drain.value', 2.), ('interfaces.on_state.heat_layer_id', 'substrate')), maximum_temperature_K=423.15, studies=studies),
            ExampleScenario('stress_semiconductor_heat', 'SiC: semiconductor heat stress (10 A)',
                'Retain original 10 A stress service while placing distributed device heat in SiC; never interpret this case as a rated device.',
                (('power.ports.drain.value', 10.), ('interfaces.on_state.heat_layer_id', 'substrate')), maximum_temperature_K=423.15,
                studies=(('power.ports.drain.value', (2., 5., 10.), 'Clearly identified current stress; not a verified ranking'),)),
        )
    return ()


def configure_device_scenario(project, identifier):
    if identifier == 'source_asymmetry':
        from etlayout.examples import rectangle
        terminal = next(item for item in project.terminals if item.id == 'source_landing_1')
        terminal.geometry = rectangle(150, 145, 230, 175)


def two_node_service(project):
    if project.power.ports:
        return len({port.external_node or port.id for port in project.power.ports if port.kind != 'floating'}) == 2
    return len([terminal for terminal in project.terminals if terminal.kind in {'voltage','ground','current','density'}]) == 2


def engineering_observations(project, result):
    """Disjoint power and local device quantities; no invented equivalent physics."""
    from etlayout.io.project import physics_hash
    if result.physics_hash != physics_hash(project):
        raise SimulationError('Engineering observations require the exact calculated project')
    power = float(result.metrics['electrical_power'])
    current = float(result.metrics['total_current'])
    feed = float(result.metrics.get('external_feed_power', 0.))
    two_node = two_node_service(project)
    budget = result.analysis['loss_budget']
    conventional = via = device = 0.
    for interface in project.interfaces:
        loss = float(result.metrics.get('contacts', {}).get(interface.id, {}).get('power', 0.))
        if interface.purpose == 'device_load' or interface.electrical_kind == 'load':
            device += loss
        elif interface.model_kind == 'physical_via':
            via += loss
        else:
            conventional += loss
    banks = {key: value for key, value in result.analysis.get('devices', {}).items() if key in {'bank_a', 'bank_b'}}
    fingers = {key: value for key, value in result.analysis.get('devices', {}).items() if '_finger_' in key}
    sources = [row['source_voltage'] for row in fingers.values() if row.get('source_voltage') is not None]
    currents = [abs(row['current']) for row in fingers.values() if row.get('current') is not None]
    sharing = ((max(currents)-min(currents))/(sum(currents)/len(currents))) if currents else None
    area = result.analysis.get('measurements', {}).get('device_loss', {}).get('support_area_m2')
    kelvin = result.terminals.get('kelvin', {}).get('voltage')
    reservoir = result.analysis.get('ports', {}).get('source', {}).get('voltage')
    return dict(configuration_hash=result.configuration_hash, physics_hash=result.physics_hash,
        numerical_convergence=result.converged, engineering_accepted=result.metrics.get('engineering_accepted'),
        current_A=current, terminal_voltage_V=result.metrics.get('voltage_drop'),
        total_input_power_W=power, lateral_sheet_power_W=float(result.metrics.get('joule_power', 0.)),
        metal_sheet_power_W=budget['categories']['routing_metal']+budget['categories']['other_metal'],
        channel_access_power_W=budget['categories']['channel']+budget['categories']['access'],
        loss_categories_W=budget['categories'],
        intrinsic_device_power_W=device, conventional_contact_power_W=conventional,
        via_power_W=via, external_feed_power_W=feed, internal_power_W=power-feed,
        terminal_resistance_ohm=power/current**2 if current and two_node else None,
        feed_deembedded_internal_resistance_ohm=(power-feed)/current**2 if current and two_node else None,
        resistance_applicability='Two distinct external power nodes' if two_node else 'Scalar resistance unavailable: multiple or unestablished external service nodes; inspect individual port responses',
        thermal_input_W=budget['thermal_deposition'], locally_deposited_feed_W=sum(
            entry['thermal_deposition'] for entry in budget['entries'] if entry['category'] == 'external_feed'),
        excluded_external_fixture_heat_W=budget['excluded_power'],
        definition='Operating-point P/I2; internal resistance removes all external feed loss. Thermal input still includes the declared local feed fraction. Not an intrinsic device material constant.',
        banks=banks, finger_current_spread_relative_to_mean=sharing,
        device_source_potential_spread_V=max(sources)-min(sources) if sources else None,
        active_device_current_density_A_cm2=current/area*1e-4 if area else None,
        passive_kelvin_offset_from_source_reservoir_V=kelvin-reservoir if kelvin is not None and reservoir is not None else None,
        heat_deposition=result.analysis.get('heat_deposition', {}),
        current_density_interpretation='GaN 2DEG uses sheet current A/m; SiC device-load uses normal A/m2; bulk metal uses A/m2 with physical thickness')
