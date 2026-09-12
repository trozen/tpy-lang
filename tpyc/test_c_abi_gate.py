"""The C-ABI representability gate on `binding="C"` signatures.

A C-linkage signature is emitted verbatim into an `extern "C"`
declaration, so a type without a C spelling either produces ill-formed
C++ or -- the reason this gate exists -- a signature that compiles but
no C caller can call. The allow-list is deliberately small; these units
pin both halves, because an over-wide gate restores a silent ABI break
and an over-narrow one rejects working interop code.
"""
from __future__ import annotations

import pytest

from tpyc.compiler import Compiler
from tpyc.diagnostics import SemanticError
from tpyc import get_lib_dir
from tpyc.typesys import (is_c_abi_allowed, c_abi_type_hint, PtrType,
                          NoneType, VoidType, INT32, BIGINT, STR, BOOL,
                          CHAR)


def test_scalars_and_pointers_are_representable():
    for t in (INT32, BOOL, CHAR, PtrType(INT32), PtrType(NoneType())):
        assert is_c_abi_allowed(t), t


def test_void_is_return_position_only():
    # `-> None` resolves to VoidType; NoneType is the annotation spelling.
    # Neither is a meaningful parameter, so the gate splits on position.
    for t in (VoidType(), NoneType()):
        assert is_c_abi_allowed(t, is_return=True)
        assert not is_c_abi_allowed(t)


def test_bigint_and_str_are_rejected():
    # The two highest-traffic mistakes: `int` is what a Python programmer
    # writes by reflex, and `str` looks C-compatible because the signature
    # can respell it `const char*` while the body still reads a view.
    for t in (BIGINT, STR):
        assert not is_c_abi_allowed(t)
        assert not is_c_abi_allowed(t, is_return=True)


@pytest.mark.parametrize("typ,fragment", [
    (BIGINT, "fixed-width integer"),
    (STR, "unsafe_str_from_cstr"),
])
def test_hint_names_the_manual_spelling(typ, fragment):
    # A bare "not representable" is useless without the remedy, and the
    # remedy differs per family.
    assert fragment in c_abi_type_hint(typ)


# The enum arm splits on `is_native`, which no predicate-level fixture can
# reach -- an EnumInfo only exists after registration -- so these two go
# through a real front-end compile. lib_dirs is load-bearing: without the
# stdlib on the path the decorator schema is absent and the parser cannot
# even see `binding="C"`.
_STDLIB_DIRS = [get_lib_dir() / "tpy"]
_ENUM_SRC = ("from tpy.extern import native, export\n"
             "from enum import Enum\n")


def _compile(src: str):
    return Compiler.from_source(src, lib_dirs=_STDLIB_DIRS).compile()


def test_native_enum_param_is_representable():
    # A @native enum names an enum the bound C header already declares, so
    # its spelling is one a C caller can write.
    _compile(_ENUM_SRC
             + "@native('Mode')\nclass Mode(Enum):\n    A = 0\n"
             + '@export(binding="C")\ndef f(m: Mode) -> None: pass\n')


def test_plain_enum_param_is_rejected():
    # A TPy-declared enum lowers to a namespaced C++ `enum class` with no C
    # spelling, so admitting it would reopen the silent-ABI hole.
    with pytest.raises(SemanticError, match="not representable in the C ABI"):
        _compile(_ENUM_SRC
                 + "class Mode(Enum):\n    A = 0\n"
                 + '@export(binding="C")\ndef f(m: Mode) -> None: pass\n')


_EXPORT = ("from tpy.extern import export\n"
           "from tpy import int32, int64, Span, Array, Ptr, readonly\n")


def _c_fn(params: str, ret: str = "None") -> str:
    return _EXPORT + f'@export(binding="C")\ndef f({params}) -> {ret}: pass\n'


# Each rejected family routes to a different remedy clause, and a wrong
# clause is worse than none -- it sends the reader at the wrong fix.
@pytest.mark.parametrize("params,fragment", [
    ("b: bytes", "Ptr[readonly[uint8]] plus an explicit length"),
    ("xs: list[int32]", "Ptr[T] plus an explicit length"),
    ("s: Span[int32]", "Ptr[T] plus an explicit length"),
    ("a: Array[int32, 2]", "Ptr[T] plus an explicit length"),
    ("t: tuple[int32, int32]", "pass it as Ptr[T]"),
    ("o: int32 | None", "pass it as Ptr[T]"),
    ("u: int32 | int64", "pass it as Ptr[T]"),
    ("n: int", "fixed-width integer"),
    ("s: str", "unsafe_str_from_cstr"),
])
def test_rejected_families_name_their_remedy(params, fragment):
    with pytest.raises(SemanticError) as exc:
        _compile(_c_fn(params))
    assert "not representable in the C ABI" in str(exc.value)
    assert fragment in str(exc.value)


def test_rejected_return_reports_as_a_return():
    # The return check is separate from the param loop, so it needs its own
    # witness -- a param-shaped message here would be a wiring mistake.
    with pytest.raises(SemanticError, match="return type 'int'"):
        _compile(_c_fn("", ret="int"))


def test_varargs_rejected_on_c_linkage():
    # A variadic C function needs a different declaration form entirely, so
    # this is its own message rather than a per-type remedy.
    with pytest.raises(SemanticError, match="variadic parameters are not"):
        _compile(_c_fn("*rest: int32"))


def test_readonly_scalar_is_unwrapped_not_rejected():
    # readonly is a TPy-side modifier with no bearing on the C spelling, so
    # the gate has to see through it rather than treat it as a new type.
    _compile(_c_fn("x: readonly[int32]"))


def test_native_global_array_checks_the_pointee():
    # The array form emits the POINTEE as the element type, so that is what
    # has to be spellable -- a separate branch from the scalar check, and
    # `Ptr[T]` would pass the scalar one unconditionally.
    src = ('from tpy.extern import native_global\n'
           'from tpy import Ptr\n'
           'g: Ptr[str] = native_global("g", binding="C", array=True)\n')
    with pytest.raises(SemanticError, match="not representable in the C ABI"):
        _compile(src)


def test_native_global_array_accepts_a_c_pointee():
    # The inverse: the unwrap must not reject a valid element type.
    src = ('from tpy.extern import native_global\n'
           'from tpy import Ptr, int16\n'
           'g: Ptr[int16] = native_global("g", binding="C", array=True)\n')
    _compile(src)


@pytest.mark.parametrize("pointee", ["list[int32]", "Widget", "int32"])
def test_ptr_is_unconditional_in_the_pointee(pointee):
    # DELIBERATE, and the only place it is asserted: a pointer is an opaque
    # handle at the ABI, so the gate constrains what crosses by value, not
    # what a pointer addresses. `list[int32]` by value is rejected two rows
    # up -- behind a Ptr it is not. Narrowing this would break the
    # documented opaque-handle convention (Ptr[SomeTpyClass]).
    src = (_EXPORT
           + "class Widget:\n    n: int32\n"
           + "    def __init__(self, n: int32) -> None: self.n = n\n"
           + f'@export(binding="C")\ndef f(p: Ptr[{pointee}]) -> None: pass\n')
    _compile(src)


def test_record_remedy_does_not_steer_a_by_value_struct_at_a_pointer():
    # extern "C" does not mangle, so a caller who follows a bare "use
    # Ptr[T]" against a by-value C struct links cleanly and the callee reads
    # a struct where a pointer was passed. The remedy has to say so.
    src = (_EXPORT
           + "class Widget:\n    n: int32\n"
           + "    def __init__(self, n: int32) -> None: self.n = n\n"
           + '@export(binding="C")\ndef f(w: Widget) -> None: pass\n')
    with pytest.raises(SemanticError, match="cannot be expressed"):
        _compile(src)
