"""Machine-wide BPM units stay consistent across application contexts."""
from dataclasses import replace

import pytest

from half_linac.src.shared.machine_profile import MachineProfile, MachineProfileError, load_app_context
from half_linac.src.apps.orbit_correct.profile_runtime import load_orbit_runtime_settings
from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config


@pytest.mark.parametrize('machine,backend,expected', [
    ('half', 'vm', 1000), ('half', 'real', 0.001),
    ('irfel', 'vm', 1000), ('irfel', 'real', 1),
])
def test_apps_share_machine_bpm_units(machine, backend, expected):
    for app in ('orbit_display', 'orbit_correct', 'bba', 'dispersion_correction'):
        context = load_app_context(app, machine_id=machine, control_backend=backend)
        assert context.machine.bpm_scale_to_mm(backend) == expected
        assert all('bpm_position_scale_to_mm' not in workflow
                   for workflow in context.profile.workflows.values())
        if app == 'orbit_correct':
            assert load_orbit_runtime_settings(context)['bpm_position_scale_to_m'] == expected * 1e-3
        if app == 'dispersion_correction':
            _, config = load_profile_run_config(context)
            assert config.backend.options['bpm_position_scale_to_mm'] == expected


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), True, 'invalid'])
def test_invalid_machine_bpm_units_rejected(value):
    with pytest.raises(MachineProfileError, match='machine.bpm_position_scale_to_mm'):
        MachineProfile.from_dict({
            'schema_version': '1',
            'machine': {'id': 'test', 'family': 'linac', 'display_name': 'Test',
                        'default_mode': 'vm', 'bpm_position_scale_to_mm': {'vm': value}},
            'elements': [], 'workflows': {},
        })


def test_missing_backend_does_not_guess_bpm_units():
    context = load_app_context('orbit_correct', machine_id='half', control_backend='real')
    machine = replace(context.machine, bpm_position_scale_to_mm={'vm': 1000})
    with pytest.raises(MachineProfileError, match='real is required'):
        machine.bpm_scale_to_mm('real')


def test_solenoid_centering_uses_machine_units():
    context = load_app_context('solenoid_centering', machine_id='half', control_backend='real')
    assert context.machine.bpm_scale_to_mm('real') == 0.001
    assert not hasattr(context.solenoid_centering_workflow, 'bpm_position_scale_to_mm')


def test_machine_scale_change_reaches_runtime_settings():
    for app in ('orbit_correct', 'dispersion_correction'):
        context = load_app_context(app, machine_id='irfel', control_backend='real')
        machine = replace(context.machine, bpm_position_scale_to_mm={'real': 2.5})
        context = replace(context, profile=replace(context.profile, machine=machine))
        if app == 'orbit_correct':
            assert load_orbit_runtime_settings(context)['bpm_position_scale_to_m'] == 0.0025
        else:
            _, config = load_profile_run_config(context)
            assert config.backend.options['bpm_position_scale_to_mm'] == 2.5
