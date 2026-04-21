"""Root-level pytest conftest.

Resets process-global compilation state before every test. The globals
(`_native_cpp_names`, `_union_alias_names`, `_protocol_modules`,
`_return_exception_names` in tpyc/typesys.py, plus the dynamic TypeDef
attachments in tpyc/type_def_registry.py) are accumulated during a
compilation. `Compiler.compile()` clears them at its start, but tests that
inspect them directly (e.g. tpyc/test_type_def_registry.py) or test cases
ordered after a case that leaves distinctive state behind would otherwise
see residue from a prior run within the same pytest-xdist worker. This
fixture makes every test start from a clean slate.
"""

import pytest

from tpyc.typesys import clear_all_compilation_state


@pytest.fixture(autouse=True)
def _reset_compilation_globals():
    clear_all_compilation_state()
    yield
