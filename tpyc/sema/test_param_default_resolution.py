"""A parameter default is resolved where it is written even when its
signature is registered after the module's defaults were drained (a
generator expression's frame function, a builder-trace function)."""
import dataclasses
from pathlib import Path

from tpyc.compilation_context import activate_compiler
from tpyc.parse import TpyFieldAccess, TpyName
from tpyc.parse.nodes import DefaultReferent
from tpyc.sema.calls import filled_default
from tpyc.thir.testutil import _compile, _entry


def test_default_registered_after_the_drain_is_resolved() -> None:
    compiler, modules = _compile(
        "from typing import Final\nfrom tpy import int32\n"
        "K: Final[int32] = 7\n"
        "def f(x: int32 = K) -> int32:\n    return x\n"
        "print(f())\n")
    entry = _entry(modules)
    f = next(fn for fn in entry.ast.functions if fn.name == "f")
    registrar = entry.analyzer.registrar
    late_default = TpyName("K", loc=f.defaults[0].loc)
    late = dataclasses.replace(f, name="late", defaults=[late_default])
    with activate_compiler(compiler):
        registrar.register_function(late)
    assert late_default.default_referent == DefaultReferent(
        module_var=(entry.analyzer.ctx.cpp_module_name, "K"))


A_USER = """\
from tpy import int32
import z_peer
from z_peer import E


def f(e: E = E.B) -> int32:
    return e.value
"""

Z_PEER = """\
import enum
import a_user


class E(enum.Enum):
    A = 1
    B = 2
"""


def test_late_default_keeps_the_drained_cycle_peers(tmp_path: Path) -> None:
    # A default registered after its import cycle drained still reads a
    # peer's storage, so it is filled at every call like the drained ones:
    # the late resolution must keep the cycle's peers.
    (tmp_path / "a_user.py").write_text(A_USER)
    (tmp_path / "z_peer.py").write_text(Z_PEER)
    compiler, modules = _compile("from a_user import f\nprint(f())\n",
                                 extra_lib_dirs=[tmp_path])
    a_user = next(m for m in modules if m.name == "a_user")
    f = next(fn for fn in a_user.ast.functions if fn.name == "f")
    drained = f.defaults[0]
    assert drained in compiler.cycle_peer_defaults
    late_default = TpyFieldAccess(obj=TpyName("E", loc=drained.loc),
                                  field="B", loc=drained.loc)
    late = dataclasses.replace(f, name="late", defaults=[late_default])
    with activate_compiler(compiler):
        a_user.analyzer.registrar.register_function(late)
        assert late_default in compiler.cycle_peer_defaults
        # The entry module names no `E`: the fill reads the stamped member.
        fill = filled_default(late_default, None,
                              _entry(modules).analyzer.ctx)
    assert isinstance(fill, TpyFieldAccess) and fill.default_fill
    assert fill.enum_member_of is late_default.enum_member_of is not None
