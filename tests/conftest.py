"""Shared test configuration.

The heavyweight tests build a full FastAPI app (``create_app``), which costs a
few seconds each. This auto-marks every test in such a module as ``integration``
so the fast unit tests can be run on their own for a quick edit/test loop:

    pytest -m "not integration"    # fast unit tests only (seconds)
    pytest                         # the whole suite (CI / pre-commit)

It only adds a marker — no test behaviour changes — so isolation is untouched.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        module = getattr(item, "module", None)
        # A module that imported create_app builds a full app in setUp → slow.
        if module is not None and hasattr(module, "create_app"):
            item.add_marker(pytest.mark.integration)
