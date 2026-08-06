"""Validate every launch file constructs a valid LaunchDescription."""

import importlib
import os
import sys

import pytest

PACKAGE_DIR = os.path.join(os.path.dirname(__file__), '..')
LAUNCH_DIR = os.path.join(PACKAGE_DIR, 'launch')


def _launch_modules():
    modules = []
    for fname in sorted(os.listdir(LAUNCH_DIR)):
        if fname.endswith('.launch.py'):
            mod_name = 'warehouse_bringup.launch.' + fname[:-3]
            modules.append((fname, mod_name))
    return modules


@pytest.mark.parametrize('fname,mod_name', _launch_modules())
def test_launch_file_parses(fname, mod_name):
    """generate_launch_description() must return a LaunchDescription."""
    sys.path.insert(0, LAUNCH_DIR)
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(LAUNCH_DIR, fname))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ld = module.generate_launch_description()
    from launch import LaunchDescription
    assert isinstance(ld, LaunchDescription)


@pytest.mark.parametrize('fname,mod_name', _launch_modules())
def test_launch_file_has_description(fname, mod_name):
    sys.path.insert(0, LAUNCH_DIR)
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(LAUNCH_DIR, fname))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ld = module.generate_launch_description()
    assert len(ld.entities) > 0
