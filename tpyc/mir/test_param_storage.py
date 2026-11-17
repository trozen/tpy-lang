"""Sema and MIR agree on which parameters are the callee's own storage.

Sema asks `param_holds_callee_copy` of a declared type (the escape checks:
a view of such a parameter cannot leave the function); MIR asks
`parameter_storage` of the passing THIR publishes and the value class. The
two must name the same parameters, or one analysis lets a dangle through
that the other refuses."""

import pytest

from ..compilation_context import activate_compiler
from ..frame_capture import frame_owns_param_copy
from ..thir import nodes as th
from ..thir.testutil import _compile, _entry
from ..typesys import param_holds_callee_copy
from .call_contract import parameter_binding, MIRParameterBinding


SOURCE = '''from typing import Callable, Iterable
from tpy import ValueType, Own, Ptr, Span, StrView, BytesView, int32

class V(ValueType):
    def __init__(self, name: str) -> None:
        self.name = name

class R:
    def __init__(self, n: int32) -> None:
        self.n = n

def by_value(x: V) -> int32:
    return len(x.name)

def owned_value(x: Own[V]) -> int32:
    return len(x.name)

def owned_ref(x: Own[R]) -> int32:
    return x.n

def owned_tuple(x: tuple[Own[R], int32]) -> int32:
    return x[1]

def opt_tuple(x: tuple[str, int32] | None) -> int32:
    return 0 if x is None else x[1]

def scalar(x: int32) -> int32:
    return x

def opt_scalar(x: int32 | None) -> int32:
    return 0 if x is None else x

def text(x: str) -> int32:
    return len(x)

def opt_text(x: str | None) -> int32:
    return 0 if x is None else len(x)

def view(x: StrView) -> int32:
    return len(x)

def opt_view(x: StrView | None) -> int32:
    return 0 if x is None else len(x)

def bytes_view(x: BytesView) -> int32:
    return len(x)

def span(x: Span[int32]) -> int32:
    return len(x)

def tup(x: tuple[str, int32]) -> int32:
    return x[1]

def union(x: str | int32) -> int32:
    return 0

def ref(x: R) -> int32:
    return x.n

def opt_ref(x: R | None) -> int32:
    return 0 if x is None else x.n

def callback(x: Callable[[], int32]) -> int32:
    return x()

def pointer(x: Ptr[V]) -> int32:
    return len(x.name)

def elements(x: Iterable[Own[R]]) -> int32:
    t = 0
    for r in x:
        t += r.n
    return t
'''

# Each parameter's declared answer: the callee holds its own copy.
EXPECTED = {
    "by_value": True, "owned_value": True, "owned_ref": True,
    # Only the owned element is the callee's (BUGS.md#own-tuple-param-element-escape).
    "owned_tuple": False,
    "opt_tuple": True, "scalar": True, "opt_scalar": True,
    "text": False, "opt_text": False, "view": False, "opt_view": False,
    "bytes_view": False, "span": False, "tup": False, "union": False,
    "ref": False, "opt_ref": False, "callback": False,
    # An owning element parameter owns what each step hands over, not the
    # iterable it binds.
    "elements": False,
    "pointer": False,
}

# Parameters MIR binds at least; it refuses to model the rest today, so only
# sema answers for them.
MIR_MODELED = frozenset({
    "owned_ref", "scalar", "opt_scalar", "text", "view", "bytes_view", "span",
    "ref", "opt_ref",
})


@pytest.fixture(scope="module")
def compiled():
    compiler, modules = _compile(SOURCE)
    entry = _entry(modules)
    _, ctx = compiler.generate_code_and_thir(entry)
    return compiler, ctx


def test_sema_and_mir_name_the_same_callee_storage(compiled) -> None:
    compiler, ctx = compiled
    with activate_compiler(compiler):
        bodies = {fn.name: fn for fn in ctx.thir_functions.values()}
        compared: set[str] = set()
        for name, expected in EXPECTED.items():
            (p,) = th.effective_params(bodies[name])
            assert param_holds_callee_copy(p.type) is expected, name
            binding = parameter_binding(p)
            if isinstance(binding, MIRParameterBinding):
                compared.add(name)
                assert binding.storage is expected, name
        assert MIR_MODELED <= compared


# A coroutine frame's own copies (`frame_owns_param_copy`), beside the
# parameters above: it also copies a `str` and every value tuple or union,
# but a view only by handle.
FRAME_COPIES = {
    "text": True, "opt_text": True, "tup": True, "union": True,
    "by_value": True, "owned_value": True, "scalar": True,
    "view": False, "span": False, "ref": False, "opt_ref": False,
    # A frame copies the pointer, not what it points at.
    "pointer": False,
    "callback": False,
    # Every element owned or a value: the frame holds the whole tuple by
    # value (a borrowed element would make it OWNED_TUPLE, held per element).
    "owned_tuple": True,
}


def test_a_coroutine_frame_copy_follows_the_frame_storage(compiled) -> None:
    compiler, ctx = compiled
    with activate_compiler(compiler):
        bodies = {fn.name: fn for fn in ctx.thir_functions.values()}
        for name, expected in FRAME_COPIES.items():
            (p,) = th.effective_params(bodies[name])
            assert frame_owns_param_copy(p.type) is expected, name
