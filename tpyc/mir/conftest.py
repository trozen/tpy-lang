"""MIR unit tests see the element facts the builtin stubs declare and the
records they build MIR over by hand."""

import pytest

from tpyc.compilation_context import activate_compiler
from tpyc.mir.testutil import register_hand_records, unregister_hand_records
from tpyc.thir.testutil import latch_builtin_stub_facts

# Test modules build MIR by hand at import, before any fixture runs.
latch_builtin_stub_facts()


def pytest_collection_finish(session) -> None:
    # Test modules register their hand-built records at import; no test
    # outside MIR's sees them in the registry.
    unregister_hand_records()


@pytest.fixture(autouse=True)
def _compiled_active(request: pytest.FixtureRequest):
    # MIR admits a record by the TypeDef its compilation registered, so a
    # module's `compiled` program lowers under the compiler that built it.
    if not hasattr(request.module, "compiled"):
        yield
        return
    with activate_compiler(request.getfixturevalue("compiled")[0]):
        yield


@pytest.fixture(autouse=True)
def _builtin_stub_facts(_reset_compilation_globals):
    latch_builtin_stub_facts()
    register_hand_records()
    yield
    unregister_hand_records()
