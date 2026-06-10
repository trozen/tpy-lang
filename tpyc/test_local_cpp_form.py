"""Tests for `CodeGenContext.local_cpp_form` and the lift-helper predicates.

The classifier reads several parallel `set[str]` fields plus
`current_func_params`. These tests stub those attributes directly to
exercise priority order and helper-method dispatch without spinning up
a full analyzer.
"""

from types import SimpleNamespace

from .codegen_cpp.context import (
    CodeGenContext,
    LocalCppForm,
)
from .typesys import (
    OwnType,
    UnionType,
    NominalType,
)


def _make_ctx(**overrides) -> SimpleNamespace:
    """Build a stub with just the attrs/methods `local_cpp_form` reads."""
    base = SimpleNamespace(
        pointer_locals=set(),
        optional_locals=set(),
        ptr_variant_locals=set(),
        storage_form_tuple_locals=set(),
        borrow_form_tuple_locals=set(),
        optional_borrow_tuple_locals=set(),
        storage_form_optional_locals=set(),
        current_func_params={},
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    # Bind the real classifier methods to the stub so the lift helpers
    # (which call `self.local_cpp_form(...)` etc.) work.
    base.is_own_ptr_variant_param = (
        lambda name: CodeGenContext.is_own_ptr_variant_param(base, name)
    )
    base.is_ptr_variant_union = (
        lambda u: CodeGenContext.is_ptr_variant_union(base, u)
    )
    base.local_cpp_form = (
        lambda name: CodeGenContext.local_cpp_form(base, name)
    )
    return base


def _form(ctx, name: str) -> LocalCppForm:
    return CodeGenContext.local_cpp_form(ctx, name)


class TestLocalCppFormClassification:
    def test_default_is_value(self):
        ctx = _make_ctx()
        assert _form(ctx, "x") is LocalCppForm.VALUE

    def test_pointer_only(self):
        ctx = _make_ctx(pointer_locals={"p"})
        assert _form(ctx, "p") is LocalCppForm.POINTER

    def test_ptr_variant(self):
        ctx = _make_ctx(ptr_variant_locals={"u"})
        assert _form(ctx, "u") is LocalCppForm.PTR_VARIANT

    def test_storage_tuple(self):
        ctx = _make_ctx(storage_form_tuple_locals={"t"})
        assert _form(ctx, "t") is LocalCppForm.STORAGE_TUPLE

    def test_borrow_tuple(self):
        # Reassigned / branch-hoisted pointer-repr tuple local: borrow form.
        ctx = _make_ctx(borrow_form_tuple_locals={"t"})
        assert _form(ctx, "t") is LocalCppForm.BORROW_TUPLE

    def test_optional_borrow_tuple(self):
        # Nullable borrow-form tuple local (`tuple[..., T] | None`):
        # std::optional<std::tuple<..., T*>>.
        ctx = _make_ctx(optional_borrow_tuple_locals={"t"})
        assert _form(ctx, "t") is LocalCppForm.OPTIONAL_BORROW_TUPLE

    def test_borrow_tuple_priority_over_optional_borrow_tuple(self):
        # A name in both sets resolves to the plain borrow tuple form (the
        # earlier-checked set); the two are producer-disjoint in practice.
        ctx = _make_ctx(borrow_form_tuple_locals={"t"},
                        optional_borrow_tuple_locals={"t"})
        assert _form(ctx, "t") is LocalCppForm.BORROW_TUPLE

    def test_storage_tuple_priority_over_borrow_tuple(self):
        # An owning generator tuple lands in both sets (storage frame slot,
        # value element access); STORAGE_TUPLE must win so reads use `.`.
        ctx = _make_ctx(storage_form_tuple_locals={"t"},
                        borrow_form_tuple_locals={"t"})
        assert _form(ctx, "t") is LocalCppForm.STORAGE_TUPLE

    def test_storage_optional(self):
        # Loop var iterating `list[P|None]` / `dict[K, P|None]`, or
        # comp/genexpr unpack var bound from a storage-form tuple slot.
        ctx = _make_ctx(storage_form_optional_locals={"it"})
        assert _form(ctx, "it") is LocalCppForm.STORAGE_OPTIONAL

    def test_optional_storage_priority_over_storage_optional(self):
        # The two sets are producer-disjoint today, but a same-name
        # shadow (Own[Opt[T_ref]] param + a loop var that reuses the
        # name) could put both populations on one name. Both lift via
        # `optional_to_ptr`; the classifier resolves to OPTIONAL_STORAGE
        # so the existing OPTIONAL_STORAGE consumer paths fire.
        ctx = _make_ctx(
            pointer_locals={"p"},
            optional_locals={"p"},
            storage_form_optional_locals={"p"},
        )
        assert _form(ctx, "p") is LocalCppForm.OPTIONAL_STORAGE

    def test_optional_storage_in_both_sets(self):
        # Own[Opt[T_ref]] params land in BOTH pointer_locals and
        # optional_locals; the classifier must return OPTIONAL_STORAGE,
        # not POINTER.
        ctx = _make_ctx(pointer_locals={"p"}, optional_locals={"p"})
        assert _form(ctx, "p") is LocalCppForm.OPTIONAL_STORAGE

    def test_value_variant_via_own_union_param(self):
        # Own[Union(Dog, Cat)] with both non-value records -> ptr-variant
        # union -> classifier returns VALUE_VARIANT for the param name.
        union = UnionType((NominalType("Dog"), NominalType("Cat")))
        own = OwnType(union)
        ctx = _make_ctx(current_func_params={"animal": own})
        # Force the union to be ptr-variant: stub `is_ptr_variant_union`
        # to return True. (The real predicate consults the record
        # registry; we bypass it here.)
        ctx.is_ptr_variant_union = lambda u: u is union
        assert _form(ctx, "animal") is LocalCppForm.VALUE_VARIANT

    def test_value_variant_takes_priority_over_ptr_variant_locals(self):
        # If a single name somehow appears in both populations, the param
        # ABI form (VALUE_VARIANT) wins -- it reflects the storage shape
        # the caller produced, not the local-decl shape.
        union = UnionType((NominalType("Dog"), NominalType("Cat")))
        own = OwnType(union)
        ctx = _make_ctx(
            ptr_variant_locals={"animal"},
            current_func_params={"animal": own},
        )
        ctx.is_ptr_variant_union = lambda u: u is union
        assert _form(ctx, "animal") is LocalCppForm.VALUE_VARIANT


class TestLiftHelpers:
    def test_needs_optional_to_ptr_lift(self):
        ctx = _make_ctx(pointer_locals={"p"}, optional_locals={"p"})
        assert CodeGenContext.needs_optional_to_ptr_lift(ctx, "p") is True
        # Plain POINTER name -- pointer_locals only, no optional_locals.
        ctx2 = _make_ctx(pointer_locals={"q"})
        assert CodeGenContext.needs_optional_to_ptr_lift(ctx2, "q") is False
        assert CodeGenContext.needs_optional_to_ptr_lift(ctx, "missing") is False

    def test_needs_to_ptr_variant_lift(self):
        union = UnionType((NominalType("Dog"), NominalType("Cat")))
        ctx = _make_ctx(current_func_params={"animal": OwnType(union)})
        ctx.is_ptr_variant_union = lambda u: u is union
        assert CodeGenContext.needs_to_ptr_variant_lift(ctx, "animal") is True
        assert CodeGenContext.needs_to_ptr_variant_lift(ctx, "x") is False
