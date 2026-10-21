"""The closure-capture warnings that need no C++ build to pin: a
module-level lambda's capture list names a namespace-scope variable, which
the strict test toolchain rejects
(BUGS.md#module-lambda-captures-namespace-variable), so the case corpus
cannot hold one."""

from __future__ import annotations

from ..diagnostics import DiagnosticLevel
from ..thir.testutil import _compile, _entry


def _warnings(source: str) -> list[str]:
    _compiler, modules = _compile(source)
    return [d.message for d in _entry(modules).analyzer.diagnostics
            if d.level is DiagnosticLevel.WARNING]


def test_a_module_lambda_reads_module_storage_in_place() -> None:
    # The lambda reads `mod_p` / `mod_s` themselves, as CPython's does: no
    # copy, cost or rebind warning, whatever the module does to them later.
    warnings = _warnings("""\
from typing import Callable
from tpy import int32
class P:
    def __init__(self, n: int32) -> None:
        self.n = n
mod_p = P(1)
mod_g: Callable[[], int32] = lambda: mod_p.n
mod_p.n = 5
mod_p = P(9)
mod_s = "a"
mod_h: Callable[[], int32] = lambda: len(mod_s)
mod_s = "abc"
mod_fs: list[Callable[[], int32]] = []
for mod_i in range(2):
    mod_fs.append(lambda: mod_i)
print(mod_g(), mod_h(), mod_fs[0]())
""")
    assert not [w for w in warnings if "mod_p" in w or "mod_s" in w], warnings
    # A module-level loop's own variable is a C++ local each lambda copies.
    assert any(w.startswith("'mod_i' is bound again on each iteration;"
                            " the lambda keeps") for w in warnings), warnings
