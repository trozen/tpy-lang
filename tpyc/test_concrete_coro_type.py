"""Unit tests for ConcreteCoroType identity preservation.

The frame-identity fields (coro_func_name/owner/type-args/module) are
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
    assert sub.coro_func_name == "fetch"
    assert sub.type_args == (INT32,)
    # inferred_type_args are compare=False extras carried verbatim; the
    # walk substitutes only type_args (identity args are re-derived at
    # the binding if ever needed).
    assert sub.coro_inferred_type_args is not None


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
