"""THIR unit tests see the element facts the builtin stubs declare."""

import pytest

from tpyc.thir.testutil import latch_builtin_stub_facts

# Test modules build types by hand at import, before any fixture runs.
latch_builtin_stub_facts()


@pytest.fixture(autouse=True)
def _builtin_stub_facts(_reset_compilation_globals):
    latch_builtin_stub_facts()
