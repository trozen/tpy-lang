"""Unit pins for the borrow-view classifier (sema/export_shape.py).

The classifier is the single answer BOTH ends of the identity/view contract
consume (sema warning suppression == glue view emission), so its gates are
pinned directly here -- notably the exact-declared-type match (an upcast
field view would lie about the dynamic type), which currently has no
case-level witness: a derived-typed by-value FIELD hits the emit-order
defect filed in BUGS.md, so no buildable case can hold one.
"""
from tpyc.parse.nodes import TpyFieldAccess, TpyName, TpyReturn
from tpyc.sema.export_shape import (
    view_safe_attr_source, view_safe_borrow_returns,
)
from tpyc.typesys import FieldInfo, NominalType, RecordInfo


class _StubRegistry:
    def __init__(self, records: dict, parents: dict | None = None):
        self._records = records
        self._parents = parents or {}

    def get_record(self, name: str):
        return self._records.get(name)

    def iter_ancestor_records(self, info):
        cur = self._parents.get(info.name)
        while cur is not None:
            yield cur
            cur = self._parents.get(cur.name)


def _record(name: str, fields=(), **kw) -> RecordInfo:
    return RecordInfo(name=name, fields=list(fields), **kw)


def _self_field(fname: str) -> TpyFieldAccess:
    return TpyFieldAccess(obj=TpyName(name="self"), field=fname)


def _setup():
    inner = _record("Inner")
    inner_sub = _record("InnerSub")
    holder = _record("Holder", fields=[
        FieldInfo(name="_inner", type=NominalType("Inner")),
        FieldInfo(name="_sub", type=NominalType("InnerSub")),
    ])
    reg = _StubRegistry(
        {"Inner": inner, "InnerSub": inner_sub, "Holder": holder},
        parents={"InnerSub": inner},
    )
    return inner, inner_sub, holder, reg


def test_exact_never_rebound_field_is_view_safe():
    inner, _sub, holder, reg = _setup()
    assert view_safe_attr_source(
        _self_field("_inner"), {"self": holder}, inner, reg)


def test_upcast_field_source_is_not_view_safe():
    # Field declared InnerSub, return typed Inner: inheritance-related but
    # NOT an exact match -- the view would slice the reported dynamic type,
    # so the classifier must push it to the copy path.
    inner, _sub, holder, reg = _setup()
    assert not view_safe_attr_source(
        _self_field("_sub"), {"self": holder}, inner, reg)


def test_rebound_field_is_not_view_safe():
    inner, _sub, holder, reg = _setup()
    holder.fields_rebound_outside_init.add("_inner")
    assert not view_safe_attr_source(
        _self_field("_inner"), {"self": holder}, inner, reg)


def test_rebind_recorded_on_declaring_ancestor_gates_derived_holder():
    # The field is DECLARED on Holder; a derived record inherits it. A view
    # request through the derived holder must consult the declarer's rebind
    # set (populated there by the sema store hook regardless of which class
    # body performed the store).
    inner, _sub, holder, reg = _setup()
    derived = _record("HolderSub")
    reg._records["HolderSub"] = derived
    reg._parents["HolderSub"] = holder
    assert view_safe_attr_source(
        _self_field("_inner"), {"self": derived}, inner, reg)
    holder.fields_rebound_outside_init.add("_inner")
    assert not view_safe_attr_source(
        _self_field("_inner"), {"self": derived}, inner, reg)


def test_non_candidate_base_name_is_not_view_safe():
    inner, _sub, holder, reg = _setup()
    expr = TpyFieldAccess(obj=TpyName(name="other"), field="_inner")
    assert not view_safe_attr_source(expr, {"self": holder}, inner, reg)


def test_value_type_return_is_not_view_safe():
    inner, _sub, holder, reg = _setup()
    inner.is_value_type = True
    assert not view_safe_attr_source(
        _self_field("_inner"), {"self": holder}, inner, reg)


class _Fn:
    def __init__(self, body):
        self.body = body


def test_borrow_returns_mixed_body_gates_on_worst_site():
    inner, _sub, holder, reg = _setup()
    good = TpyReturn(value=_self_field("_inner"))
    bare = TpyReturn(value=TpyName(name="self"))
    assert view_safe_borrow_returns(
        _Fn([bare, good]), {"self": holder}, inner, reg)
    bad = TpyReturn(value=_self_field("_sub"))
    assert not view_safe_borrow_returns(
        _Fn([bare, good, bad]), {"self": holder}, inner, reg)
