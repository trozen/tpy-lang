"""Nested signature types require complete owners before factory declarations."""

import re

from .. import get_lib_dir
from ..compilation_context import activate_compiler
from ..compiler import Compiler
from ..parse import TpyFunction
from .generator import _func_uses_nested_type, _references_nested_type


def test_nested_signature_predicate_traverses_wrappers_and_return_types() -> None:
    source = '''
from enum import Enum
from tpy import Own, readonly, Ptr, Span
from tplib.box import Box
from tplib.rc import Rc

class Outer:
    class Inner:
        value: int
    class Kind(Enum):
        ONE = 1

class Flat:
    value: int

def wrapped(
    direct: Outer.Inner, enum: Outer.Kind,
    one: tuple[Outer.Inner], many: tuple[int, Outer.Inner],
    optional: Outer.Inner | None, union: Outer.Inner | Flat,
    elements: list[Outer.Inner], owned: Own[Outer.Inner],
    shared: readonly[Outer.Inner], pointer: Ptr[Outer.Inner],
    span: Span[Outer.Inner], boxed: Box[Outer.Inner], counted: Rc[Outer.Inner],
) -> None:
    pass

def inverse(value: tuple[list[Flat], int | None]) -> None:
    pass
'''
    compiler = Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = next(m for m in modules if m.is_entry_point)
    functions = {func.name: func for func in entry.ast.functions}
    with activate_compiler(compiler):
        for name, typ in functions["wrapped"].params:
            assert _references_nested_type(typ), name
            # Return-only signatures use the same conservative scheduling rule.
            returning = TpyFunction("returning", [], typ, [])
            assert _func_uses_nested_type(returning), name
        assert _func_uses_nested_type(functions["wrapped"])
        assert not _func_uses_nested_type(functions["inverse"])
    hpp, _ = compiler.generate_code_to_strings(entry)
    outer = hpp.index("struct Outer {")
    complete = hpp.index("\n};", outer) + len("\n};")
    assert hpp.index("void inverse(") < outer
    assert hpp.index("void wrapped(") > complete
    assert not compiler.diagnostics


def test_resumable_forward_declarations_wait_for_nested_signature_owners() -> None:
    source = '''
from typing import Iterator

class Outer:
    class Inner:
        value: int

async def coro(x: Outer.Inner, n: int = 3) -> int:
    return x.value + n

def gen(x: Outer.Inner, n: int = 3) -> Iterator[int]:
    yield x.value
    yield n

def sync(x: Outer.Inner, n: int = 3) -> int:
    return x.value + n

def simple(x: Outer.Inner) -> Iterator[int]:
    for n in range(2):
        yield x.value

async def generic[T](x: Outer.Inner, value: T) -> T:
    return value

def generic_gen[T](x: Outer.Inner, value: T) -> Iterator[T]:
    yield value
    yield value

async def early(n: int) -> int:
    return n

def early_gen(n: int) -> Iterator[int]:
    yield n
    yield n

def early_sync(n: int) -> int:
    return n
'''
    compiler = Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = next(m for m in modules if m.is_entry_point)
    hpp, cpp = compiler.generate_code_to_strings(entry)
    outer = hpp.index("struct Outer {")
    complete = hpp.index("\n};", outer) + len("\n};")
    for name, frame in (("coro", "__coro_coro"), ("gen", "__gen_gen"),
                        ("generic", "__coro_generic"), ("generic_gen", "__gen_generic_gen")):
        assert hpp.index(f"struct {frame};") < outer
        declarations = list(re.finditer(rf"^{frame}(?:<T>)? {name}\(.*\);$", hpp, re.M))
        assert len(declarations) == 1, name
        declaration = declarations[0]
        assert declaration.start() > complete, name
        if name.startswith("generic"):
            assert hpp[:declaration.start()].endswith("template <typename T>\n")
        else:
            assert "n = ::tpy::BigInt(3)" in declaration.group(), name
    assert hpp.index("::tpy::BigInt sync(") > complete
    for spelling in ("__coro_early early(", "__gen_early_gen early_gen(",
                     "::tpy::BigInt early_sync("):
        assert hpp.index(spelling) < outer
    # A single-yield generator is a frame like any other: a named struct
    # forward and one factory forward after the nested owner completes.
    assert hpp.index("struct __gen_simple;") < outer
    simple_decls = list(re.finditer(r"^__gen_simple simple\(.*\);$", hpp, re.M))
    assert len(simple_decls) == 1 and simple_decls[0].start() > complete
    # Once per declaration a TPy call can land on: the factory forward
    # decl AND the frame ctor for `coro` and `gen` (an inline await or a
    # synthetic suspension constructs the frame directly), plus `sync`.
    assert hpp.count(" = ::tpy::BigInt(3)") == 5
    assert " = ::tpy::BigInt(3)" not in cpp
    assert not compiler.diagnostics
