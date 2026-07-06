"""Unit coverage for the distinct-shape tally (shape.py): signature invariance
across structural twins, distinctness across construct/type differences, and the
summarize reduction."""

from __future__ import annotations

from ..typesys import INT32, VoidType
from .shape import _type_family, body_shape_signature, summarize_shapes


class _FakeFunc:
    def __init__(self, body, params, return_type, is_method=False,
                 is_staticmethod=False, is_property_getter=False,
                 is_property_setter=False):
        self.body = body
        self.params = params
        self.return_type = return_type
        self.is_method = is_method
        self.is_staticmethod = is_staticmethod
        self.is_property_getter = is_property_getter
        self.is_property_setter = is_property_setter


def _return_name():
    # A minimal real AST body: `return x`.
    from ..parse.nodes import TpyName, TpyReturn
    return [TpyReturn(value=TpyName(name="x"))]


class TestTypeFamily:
    def test_scalar_and_void(self):
        assert _type_family(INT32) == "scalar"
        assert _type_family(VoidType()) == "void"
        assert _type_family(None) == "void"

    def test_generic_and_own(self):
        from ..typesys import OwnType, TypeParamRef
        assert _type_family(TypeParamRef("T")) == "generic"
        assert _type_family(OwnType(TypeParamRef("T"))) == "own[generic]"


class TestSignature:
    def test_twins_collapse(self):
        # Two functions with the same construct set + signature families produce
        # the same signature -- the cross-module / stdlib-repeat dedup.
        a = _FakeFunc(_return_name(), [("x", INT32)], INT32)
        b = _FakeFunc(_return_name(), [("y", INT32)], INT32)
        assert body_shape_signature(a, "body") == body_shape_signature(b, "body")

    def test_return_family_distinguishes(self):
        # Same body/params, different return family -> different shape.
        from ..typesys import TypeParamRef
        a = _FakeFunc(_return_name(), [("x", INT32)], INT32)
        b = _FakeFunc(_return_name(), [("x", INT32)], TypeParamRef("T"))
        assert body_shape_signature(a, "body") != body_shape_signature(b, "body")

    def test_kind_distinguishes(self):
        a = _FakeFunc(_return_name(), [("x", INT32)], INT32)
        m = _FakeFunc(_return_name(), [("x", INT32)], INT32, is_method=True)
        assert body_shape_signature(a, "body") != body_shape_signature(m, "body")
        assert body_shape_signature(a, "ctor").startswith("ctor|")


class TestSummarize:
    def test_routed_partial_blocked(self):
        shapes = {
            "s_routed": {"routed": 5},
            "s_blocked": {"sig.param_type": 3},
            "s_partial": {"routed": 2, "sig.return_type": 1},
        }
        out = summarize_shapes(shapes)
        assert out["total"] == 3
        assert out["routed"] == 1           # only s_routed is fully routed
        assert out["partial"] == 1          # s_partial has both
        assert len(out["blocked"]) == 2     # s_blocked + s_partial
        assert abs(out["pct"] - 100.0 / 3) < 1e-6

    def test_blocked_ranked_by_leverage(self):
        shapes = {
            "a": {"sig.param_type": 10},
            "b": {"sig.return_type": 50},
        }
        out = summarize_shapes(shapes)
        assert out["blocked"][0][0] == "b"  # highest leverage first
        assert out["blocked"][0][1] == 50
