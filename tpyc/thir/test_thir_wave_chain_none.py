"""Pins for the one-link CHAIN Optional-field family: the None-test
subject (`o.inner.value is not None` -> `.has_value()` over the bare
chain read), the narrowed chain READ (`(*o.inner.value)` -- the
stateless declared-vs-analyzed verdict is receiver-shape-blind), and the
truthy flavor (`if o.inner.value:` -> `is_truthy(...)`). The
intermediate link must be a plain DECLARED record
(_plain_record_field_link, shared by both admissions). Boundaries:
two-link chains and Optional intermediate links keep deferring."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical,
    _assert_routes_byte_identical,
    _fn,
    _lower_ctx_witnessed,
)

_HDR = (
    "from tpy import Int32\n"
    "class Inner:\n"
    "    value: Int32 | None\n"
    "    def __init__(self, v: Int32 | None) -> None:\n"
    "        self.value = v\n"
    "class Outer:\n"
    "    inner: Inner\n"
    "    def __init__(self, inner: Inner) -> None:\n"
    "        self.inner = inner\n"
)


class TestChainNoneSubject:
    def test_chain_none_test_and_narrowed_read_route(self):
        src = _HDR + (
            "def get_value(o: Outer) -> Int32:\n"
            "    if o.inner.value is not None:\n"
            "        return o.inner.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(get_value(Outer(Inner(5))))\n"
        )
        _assert_routes_byte_identical(src)

    def test_chain_truthy_routes(self):
        src = _HDR + (
            "def get_value(o: Outer) -> Int32:\n"
            "    if o.inner.value:\n"
            "        return o.inner.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(get_value(Outer(Inner(5))))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "get_value") is not None
        assert faces["truthy.optional_field_whole"] >= 1
        assert faces["field.narrowed_deref"] >= 1
        _assert_byte_identical(src)


class TestChainNoneBoundaries:
    def test_two_link_chain_still_defers(self):
        src = _HDR + (
            "class Top:\n"
            "    outer: Outer\n"
            "    def __init__(self, o: Outer) -> None:\n"
            "        self.outer = o\n"
            "def get_value(t: Top) -> Int32:\n"
            "    if t.outer.inner.value is not None:\n"
            "        return t.outer.inner.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(get_value(Top(Outer(Inner(5)))))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "get_value") is None
        _assert_byte_identical(src)

    def test_narrowed_read_predicate_is_link_blind(self):
        # The READ predicate is deliberately LINK-BLIND (the AST's
        # is_narrowed_optional_field derefs on the leaf's
        # declared-vs-analyzed mismatch alone; a link-type rule here
        # DE-ROUTED dataclass_asdict_mixed's macro chain read -- ratchet
        # catch). The predicate returns True even for an
        # Optional-declared link; body-level exclusion of the whole shape
        # is the SUBJECT gates' job (test_optional_intermediate_link_
        # still_defers). Built on the real compile: the chain node is
        # pulled from the parsed AST, the leaf's rtype passed as its
        # narrowed (non-Optional) inner.
        from .lower.predicates import _narrowed_opt_field_read
        from ..parse.nodes import TpyFieldAccess, TpyReturn
        from .testutil import _compile, _entry
        src = _HDR + (
            "class OptTop:\n"
            "    inner: Inner | None\n"
            "    def __init__(self, i: Inner) -> None:\n"
            "        self.inner = i\n"
            "def get_value(t: OptTop) -> Int32:\n"
            "    if t.inner is not None and t.inner.value is not None:\n"
            "        return t.inner.value\n"
            "    return 0\n"
        )
        compiler, modules = _compile(src)
        mod = _entry(modules)
        fn = next(f for f in mod.ast.functions if f.name == "get_value")

        def _find_chain(stmts):
            for s in stmts:
                if (isinstance(s, TpyReturn)
                        and isinstance(s.value, TpyFieldAccess)
                        and isinstance(s.value.obj, TpyFieldAccess)):
                    return s.value
                for attr in ("body", "then_body", "else_body"):
                    inner = getattr(s, attr, None)
                    if inner:
                        found = _find_chain(inner)
                        if found is not None:
                            return found
            return None

        chain = _find_chain(fn.body)
        assert chain is not None
        analyzer = mod.analyzer
        rtype = analyzer.get_expr_type(chain)
        locals_ = {n: t for n, t in fn.params}
        assert _narrowed_opt_field_read(
            chain, rtype, locals_, analyzer) is True

    def test_optional_intermediate_link_still_defers(self):
        # An Optional-declared intermediate link reads with an unwrap the
        # chain rule excludes (_plain_record_field_link).
        src = _HDR + (
            "class OptTop:\n"
            "    inner: Inner | None\n"
            "    def __init__(self, i: Inner) -> None:\n"
            "        self.inner = i\n"
            "def get_value(t: OptTop) -> Int32:\n"
            "    if t.inner is not None and t.inner.value is not None:\n"
            "        return t.inner.value\n"
            "    return 0\n"
            "def main() -> None:\n"
            "    print(get_value(OptTop(Inner(5))))\n"
        )
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "get_value") is None
        _assert_byte_identical(src)
