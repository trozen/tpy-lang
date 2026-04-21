"""Tests for sema.method_expansion.

The per-record entry point (`expand_methods_for_record`) is invoked from
`register_record` after macros run, so macro-added methods flow through
the same self-flag derivation / wrapping / cloning pipeline as
source-authored methods (Phase F.3b.6.5). These tests pin the
idempotency contract that the per-record call relies on.
"""

from __future__ import annotations

import pytest

from .parse.nodes import TpyFunction, TpyRecord, RecordLinkage
from .diagnostics import SemanticError
from .sema.method_expansion import expand_methods_for_record
from .typesys import (
    AutoReadonlyType,
    AutoOwnType,
    OwnType,
    SelfType,
    NominalType,
    SELF,
    VOID,
    INT32,
)


def _make_record(methods: list[TpyFunction]) -> TpyRecord:
    return TpyRecord(
        name="R",
        fields=[],
        methods=methods,
        bases=[],
        linkage=RecordLinkage.DEFAULT,
    )


def _make_method(**kwargs) -> TpyFunction:
    return TpyFunction(
        name=kwargs.pop("name", "m"),
        params=kwargs.pop("params", []),
        return_type=kwargs.pop("return_type", VOID),
        body=[],
        is_method=True,
        **kwargs,
    )


class TestAutoReadonlyDecoratorExpansion:
    def test_decorator_expands_to_mutable_and_const_clones(self):
        method = _make_method(
            name="get",
            return_type=INT32,
            has_auto_readonly_decorator=True,
        )
        record = _make_record([method])

        expand_methods_for_record(record)

        assert len(record.methods) == 2
        mutable, const = record.methods
        assert mutable.is_auto_readonly_mutable_clone
        assert not mutable.is_readonly
        assert not const.is_auto_readonly_mutable_clone
        assert const.is_readonly
        # Both clones are "expansion done" and carry no derivation source.
        assert mutable.self_annotation is None
        assert const.self_annotation is None
        assert not mutable.has_auto_readonly_decorator
        assert not const.has_auto_readonly_decorator

    def test_decorator_expansion_is_idempotent(self):
        method = _make_method(
            name="get",
            return_type=INT32,
            has_auto_readonly_decorator=True,
        )
        record = _make_record([method])

        expand_methods_for_record(record)
        first_count = len(record.methods)
        expand_methods_for_record(record)
        assert len(record.methods) == first_count


class TestAutoReadonlySelfExpansion:
    def test_self_auto_readonly_clones_and_strips_annotation(self):
        method = _make_method(
            name="m",
            self_annotation=AutoReadonlyType(SELF),
        )
        record = _make_record([method])

        expand_methods_for_record(record)

        assert len(record.methods) == 2
        # A second expansion must not re-clone -- self_annotation is nulled
        # on both halves so _derive_self_flags doesn't re-set auto_readonly.
        expand_methods_for_record(record)
        assert len(record.methods) == 2


class TestAutoOwnSelfExpansion:
    def test_self_auto_own_clones_and_strips_annotation(self):
        method = _make_method(
            name="take",
            self_annotation=AutoOwnType(SELF),
            return_type=AutoOwnType(NominalType("R")),
        )
        record = _make_record([method])

        expand_methods_for_record(record)

        assert len(record.methods) == 2
        borrowing, consuming = record.methods
        assert borrowing.is_auto_own_borrowing_clone
        assert not borrowing.is_consuming
        assert consuming.is_consuming
        assert borrowing.self_annotation is None
        assert consuming.self_annotation is None

        expand_methods_for_record(record)
        assert len(record.methods) == 2


class TestConsumingSelfNotCloned:
    def test_own_self_sets_is_consuming_no_clone(self):
        method = _make_method(
            name="take",
            self_annotation=OwnType(SELF),
        )
        record = _make_record([method])

        expand_methods_for_record(record)

        assert len(record.methods) == 1
        assert record.methods[0].is_consuming
        # self_annotation is cleared after derivation so a second pass
        # doesn't re-fire `_derive_self_flags` on it (symmetric with the
        # clone paths).
        assert record.methods[0].self_annotation is None

        expand_methods_for_record(record)
        assert len(record.methods) == 1
        assert record.methods[0].is_consuming


class TestSelfAnnotationValidation:
    def test_own_self_on_init_raises_semantic_error(self):
        method = _make_method(
            name="__init__",
            self_annotation=OwnType(SELF),
        )
        record = _make_record([method])

        with pytest.raises(SemanticError, match="Own\\[Self\\] is not allowed on '__init__'"):
            expand_methods_for_record(record)

    def test_own_self_on_del_raises_semantic_error(self):
        method = _make_method(
            name="__del__",
            self_annotation=OwnType(SELF),
        )
        record = _make_record([method])

        with pytest.raises(SemanticError, match="Own\\[Self\\] is not allowed on '__del__'"):
            expand_methods_for_record(record)

    def test_auto_readonly_self_on_init_raises_semantic_error(self):
        method = _make_method(
            name="__init__",
            self_annotation=AutoReadonlyType(SELF),
        )
        record = _make_record([method])

        with pytest.raises(SemanticError, match="auto_readonly\\[Self\\] is not allowed on '__init__'"):
            expand_methods_for_record(record)

    def test_own_self_with_readonly_raises_semantic_error(self):
        method = _make_method(
            name="m",
            self_annotation=OwnType(SELF),
            is_readonly=True,
        )
        record = _make_record([method])

        with pytest.raises(SemanticError, match="Own\\[Self\\] cannot be combined with @readonly"):
            expand_methods_for_record(record)


class TestPerParamAutoReadonlyTriggersCloning:
    """Per-param `auto_readonly[T]` annotation alone (no decorator, no self:
    auto_readonly[Self]) must still set `auto_readonly=True` in sema and
    trigger cloning. Regression path for F.3b.6.2 which moved the per-param
    detection out of the parser.
    """

    def test_per_param_auto_readonly_clones(self):
        # Method with a param whose type carries AutoReadonlyType.
        method = _make_method(
            name="pick",
            params=[("x", AutoReadonlyType(NominalType("T")))],
            return_type=INT32,
        )
        record = _make_record([method])

        expand_methods_for_record(record)

        # Cloned into mutable + const halves.
        assert len(record.methods) == 2
        mutable, const = record.methods
        assert mutable.is_auto_readonly_mutable_clone
        assert not mutable.is_readonly
        assert not const.is_auto_readonly_mutable_clone
        assert const.is_readonly
        # Mutable clone strips the AutoReadonlyType; const clone applies it.
        from .typesys import ReadonlyType, strip_auto_readonly, apply_auto_readonly
        _, mut_ptype = mutable.params[0]
        _, const_ptype = const.params[0]
        assert not isinstance(mut_ptype, AutoReadonlyType)
        assert isinstance(const_ptype, ReadonlyType)


class TestMacroAddedMethodExpansion:
    """Simulates the macro-added method flow: add a method post-parse and
    verify it flows through the same pipeline when the per-record entry
    point runs. This is the regression the F.3b.6.5 change protects.
    """

    def test_method_added_after_initial_expansion_gets_expanded(self):
        # First method: already expanded form (simulates parser output
        # that the module-level caller might have processed before
        # macros add theirs). It stays put.
        existing = _make_method(name="existing", return_type=INT32)
        record = _make_record([existing])
        expand_methods_for_record(record)
        assert len(record.methods) == 1

        # Macro adds a new method with @auto_readonly decorator flag.
        new_method = _make_method(
            name="new_method",
            return_type=INT32,
            has_auto_readonly_decorator=True,
        )
        record.methods.append(new_method)

        expand_methods_for_record(record)

        # existing stays as 1 method; new_method cloned into 2. Total 3.
        assert len(record.methods) == 3
        names = [m.name for m in record.methods]
        assert names.count("existing") == 1
        assert names.count("new_method") == 2
