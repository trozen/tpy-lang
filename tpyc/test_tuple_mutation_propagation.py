"""Phase 0 RED tests: mutation propagation through tuple destructure
and through call edges where the param is a tuple/Optional.

These pin the prerequisite facts that the const-inference work depends on.
They were RED before the Phase 0 fixes (predicate at calls.py:2470 +
borrow-tracker edges in _analyze_tuple_unpack).

If any of these regresses, the const-inference downstream becomes unsound:
a body that mutates through a slot would be silently inferred read-only
and emitted with const slots.
"""

from . import get_lib_dir
from .compiler import Compiler

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


def _compile(source: str):
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    modules = compiler.compile()
    entry = [m for m in modules if m.is_entry_point][0]
    return entry


def _get_func(module, name: str):
    fi_list = module.exports.functions[name]
    assert len(fi_list) == 1, f"expected single overload of {name}, got {len(fi_list)}"
    return fi_list[0]


class TestTupleDestructureMutationPropagation:
    """`a, b = p; a.x = ...` must mark `p` mutated."""

    def test_tuple_of_record_destructure_field_write(self):
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def f(p: tuple[T, T]) -> None:
    a, b = p
    a.x = int32(1)
"""
        module = _compile(source)
        f = _get_func(module, "f")
        assert f.mutated_params is not None
        assert 0 in f.mutated_params, (
            f"`p` should be marked mutated after destructure+field-write, "
            f"got mutated_params={f.mutated_params}"
        )

    def test_tuple_of_optional_record_destructure_narrowing_field_write(self):
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def f(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        a.x = int32(1)
"""
        module = _compile(source)
        f = _get_func(module, "f")
        assert f.mutated_params is not None
        assert 0 in f.mutated_params, (
            f"`p` should be marked mutated after destructure+narrowing+field-write, "
            f"got mutated_params={f.mutated_params}"
        )

    def test_tuple_of_record_destructure_no_write_not_mutated(self):
        """Negative: pure read through destructure must not mark mutated."""
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def f(p: tuple[T, T]) -> int32:
    a, b = p
    return a.x + b.x
"""
        module = _compile(source)
        f = _get_func(module, "f")
        assert f.mutated_params is not None
        assert 0 not in f.mutated_params, (
            f"`p` should NOT be marked mutated for pure read access, "
            f"got mutated_params={f.mutated_params}"
        )


class TestTupleTransitiveMutationPropagation:
    """Phase-2 propagation: if inner mutates a tuple param, outer's matching
    param must also be marked mutated when it forwards through inner."""

    def test_transitive_through_tuple_param(self):
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def inner(p: tuple[T, T]) -> None:
    a, b = p
    a.x = int32(1)

def outer(p: tuple[T, T]) -> None:
    inner(p)
"""
        module = _compile(source)
        inner = _get_func(module, "inner")
        outer = _get_func(module, "outer")
        assert inner.mutated_params is not None and 0 in inner.mutated_params
        assert outer.mutated_params is not None and 0 in outer.mutated_params, (
            f"outer's `p` should be transitively marked mutated, "
            f"got mutated_params={outer.mutated_params}"
        )

    def test_transitive_through_optional_tuple_param(self):
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def inner(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        a.x = int32(1)

def outer(p: tuple[T | None, T | None]) -> None:
    inner(p)
"""
        module = _compile(source)
        inner = _get_func(module, "inner")
        outer = _get_func(module, "outer")
        assert inner.mutated_params is not None and 0 in inner.mutated_params
        assert outer.mutated_params is not None and 0 in outer.mutated_params, (
            f"outer's `p` should be transitively marked mutated, "
            f"got mutated_params={outer.mutated_params}"
        )

    def test_no_propagation_when_inner_does_not_mutate(self):
        """Negative: if inner only reads the tuple, outer is not marked."""
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def inner(p: tuple[T, T]) -> int32:
    a, b = p
    return a.x + b.x

def outer(p: tuple[T, T]) -> int32:
    return inner(p)
"""
        module = _compile(source)
        outer = _get_func(module, "outer")
        assert outer.mutated_params is not None
        assert 0 not in outer.mutated_params


class TestTupleLiteralRhsDestructure:
    """`a, b = (x, y)` should also propagate mutation through the borrow
    edges from x/y to a/b."""

    def test_tuple_literal_rhs_propagates(self):
        source = """
from tpy import int32

class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def f(x: T, y: T) -> None:
    a, b = (x, y)
    a.x = int32(1)
"""
        module = _compile(source)
        f = _get_func(module, "f")
        assert f.mutated_params is not None
        assert 0 in f.mutated_params, (
            f"`x` should be marked mutated through tuple-literal destructure, "
            f"got mutated_params={f.mutated_params}"
        )
        # `y` is destructured into b but b is never written through.
        assert 1 not in f.mutated_params, (
            f"`y` should NOT be marked mutated, got mutated_params={f.mutated_params}"
        )
