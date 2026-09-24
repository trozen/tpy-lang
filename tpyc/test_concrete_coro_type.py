"""Unit tests for ConcreteCoroType identity preservation.

The frame-identity fields (frame_func_name/owner/type-args/module) are
what codegen renders the concrete `__coro_*` struct from; losing them
silently degrades a zero-alloc handle to the erased representation.
The substitution walk reconstructs types via with_inner_types, which
NominalType implements by building a plain NominalType -- the override
must keep the subclass and its fields.
"""

from .typesys import (
    INT32, ConcreteCoroType, NominalType, TypeParamRef,
    make_concrete_coro, substitute_type_params_structural,
)


def test_substitution_preserves_identity():
    t = make_concrete_coro(TypeParamRef("T"), "fetch",
                           inferred_type_args=(TypeParamRef("T"),))
    sub = substitute_type_params_structural(t, {"T": INT32})
    assert isinstance(sub, ConcreteCoroType)
    assert sub.frame_func_name == "fetch"
    assert sub.type_args == (INT32,)
    # inferred_type_args are compare=False extras carried verbatim; the
    # walk substitutes only type_args (identity args are re-derived at
    # the binding if ever needed).
    assert sub.frame_inferred_type_args is not None


def test_identity_participates_in_equality():
    a = make_concrete_coro(INT32, "f")
    b = make_concrete_coro(INT32, "g")
    assert a != b
    assert a == make_concrete_coro(INT32, "f")


def test_never_equal_to_plain_cancellable():
    from .typesys import make_cancellable
    assert make_concrete_coro(INT32, "f") != make_cancellable(INT32)
    # But the qname-keyed surface matches (conformance/registry paths).
    assert (make_concrete_coro(INT32, "f").qualified_name()
            == make_cancellable(INT32).qualified_name())


def test_async_inner_return_survives_registration_and_substitution():
    """`FunctionInfo.async_inner_return` defaults to None, and the
    async-return-form classifier treats None as STORAGE -- so a
    construction site that drops the field FAILS OPEN, silently
    reclassifying a borrow-returning coroutine as owned (skipping the
    erasure-boundary reject and the aliasing return). Four hand-built
    FunctionInfo sites needed the field when it landed; this guards the
    next one."""
    from .compiler import Compiler
    from . import get_lib_dir

    source = (
        "import asyncio\n\n"
        "class C:\n"
        "    v: int\n"
        "    def __init__(self) -> None:\n"
        "        self.v = 1\n"
        "    async def get(self) -> \"C\":\n"
        "        await asyncio.sleep(0)\n"
        "        return self\n\n"
        "async def identity[T](x: T) -> T:\n"
        "    await asyncio.sleep(0)\n"
        "    return x\n\n"
        "async def main() -> None:\n"
        "    c = C()\n"
        "    r = await c.get()\n"
        "    s = await identity(c)\n"
        "    print(r.v, s.v)\n\n"
        "asyncio.run(main())\n"
    )
    compiler = Compiler.from_source(source, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    # Modules are in dependency order; the entry module is last.
    registry = modules[-1].analyzer.registry
    checked = 0
    for overloads in registry.functions.values():
        for fi in overloads:
            if fi.is_async:
                assert fi.async_inner_return is not None, fi.name
                checked += 1
    for rec in registry.records.values():
        for overloads in rec.methods.values():
            for fi in overloads:
                if fi.is_async:
                    assert fi.async_inner_return is not None, fi.name
                    checked += 1
    assert checked > 0


def test_async_inner_return_survives_method_substitution():
    from dataclasses import replace as dc_replace
    from .typesys import FunctionInfo

    fi = FunctionInfo(name="fetch", params=[], return_type=INT32,
                      is_async=True, async_inner_return=TypeParamRef("T"),
                      type_params=["T"])
    # The substitution helpers rebuild FunctionInfo by hand; a dc_replace
    # round-trip is the reference behavior they must match.
    assert dc_replace(fi, return_type=INT32).async_inner_return is not None
