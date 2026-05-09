"""Unit tests for the centralized const decision helper."""

from ..typesys import (
    INT32, BOOL, STR, BIGINT,
    NominalType, OptionalType, TupleType, OwnType, ReadonlyType, RefType,
    TypeParamRef, TypeParamKind,
)
from .param_const import decide_param_const, ParamConstDecision


T = NominalType("T")
TUPLE_T_T = TupleType((T, T))
TUPLE_OPT_T = TupleType((OptionalType(T), OptionalType(T)))
OPT_T = OptionalType(T)


def _decide(ptype, **kwargs):
    """decide_param_const helper that defaults common args."""
    kwargs.setdefault("index", 0)
    kwargs.setdefault("pname", "p")
    kwargs.setdefault("mutated_params", frozenset())
    return decide_param_const(ptype, **kwargs)


class TestSingleRecord:
    def test_unmutated_borrow_is_const(self):
        d = _decide(T)
        assert d == ParamConstDecision(True, True)

    def test_mutated_borrow_is_not_const(self):
        d = _decide(T, mutated_params=frozenset({0}))
        assert d == ParamConstDecision(False, False)

    def test_unanalyzed_is_not_const(self):
        d = _decide(T, mutated_params=None)
        assert d == ParamConstDecision(False, False)

    def test_addr_escape_is_not_const(self):
        d = _decide(T, addr_escapes_params=frozenset({0}))
        assert d == ParamConstDecision(False, False)

    def test_explicit_readonly_is_const(self):
        d = _decide(ReadonlyType(T), mutated_params=None)
        assert d == ParamConstDecision(True, True)

    def test_const_params_forces_const(self):
        d = _decide(T, mutated_params=None, const_params=True)
        assert d == ParamConstDecision(True, True)


class TestSingleOptional:
    def test_unmutated_pointer_optional_is_const(self):
        d = _decide(OPT_T)
        assert d == ParamConstDecision(True, True)

    def test_mutated_pointer_optional_is_not_const(self):
        d = _decide(OPT_T, mutated_params=frozenset({0}))
        assert d == ParamConstDecision(False, False)

    def test_value_optional_is_not_const(self):
        # Optional[int] uses std::optional<int>, not pointer repr.
        opt_int = OptionalType(INT32)
        d = _decide(opt_int)
        assert d == _NOT_CONST


class TestTupleParam:
    def test_unmutated_tuple_of_records_is_deep_const(self):
        d = _decide(TUPLE_T_T)
        assert d == ParamConstDecision(True, True)

    def test_unmutated_tuple_of_optional_records_is_deep_const(self):
        d = _decide(TUPLE_OPT_T)
        assert d == ParamConstDecision(True, True)

    def test_mutated_tuple_is_not_const(self):
        d = _decide(TUPLE_T_T, mutated_params=frozenset({0}))
        assert d == ParamConstDecision(False, False)

    def test_value_only_tuple_is_not_const(self):
        # tuple[int, int] -- no borrowed surface.
        d = _decide(TupleType((INT32, BOOL)))
        assert d == _NOT_CONST

    def test_generic_slot_tuple_is_not_inferred(self):
        # tuple[T, T] where T is unbounded -- mutation surface is unknowable
        # at the template level. Inference must default to not-const.
        T_param = TypeParamRef(name="T", kind=TypeParamKind.TYPE)
        d = _decide(TupleType((T_param, T_param)))
        assert d == _NOT_CONST


class TestTypeParamTopLevel:
    def test_top_level_typeparam_is_not_inferred(self):
        # def f[T](x: T): ... -- spelling decided at instantiation, not now.
        T_param = TypeParamRef(name="T", kind=TypeParamKind.TYPE)
        d = _decide(T_param)
        assert d == _NOT_CONST


class TestReassignedParams:
    def test_reassigned_str_is_not_const(self):
        # str needs copy-for-reassign.
        d = _decide(STR, reassigned_params={"p"})
        assert d == _NOT_CONST

    def test_reassigned_record_can_still_be_const(self):
        # Records don't need copy-for-reassign; can rebind a const ref.
        d = _decide(T, reassigned_params={"p"})
        assert d.signature_const


_NOT_CONST = ParamConstDecision(False, False)
