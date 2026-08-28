"""A value-repr `Optional[value tuple]` local REASSIGNED from a whole-optional
source (`use_auth = self.auth`).

The slot is `std::optional<std::tuple<..>>` by value, so the copy is the same
bare assign the scalar and owned-view kinds already take. Two halves were
missing: the reassign sink clamped its whole-optional grant to the REGISTERED
binding kinds (scalar / view), and the field ladder's whole-optional result
row did not list the tuple inner. The target is asked off its DECLARED type
rather than the binding registry on purpose -- the tuple kind admits the
whole-optional copy only. Corpus witness: `tplib.requests.Session.request`."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry,
                       _lower_ctx_witnessed)
from ..codegen_cpp import CodeGenOptions


_SRC = ("from tpy import Int32\n"
        "class Cfg:\n"
        "    auth: tuple[str, str] | None\n"
        "    pair: tuple[Int32, Int32] | None\n"
        "    xs: list[Int32] | None\n"
        "    plain: tuple[Int32, Int32]\n"
        "    def __init__(self) -> None:\n"
        "        self.auth = None\n"
        "        self.pair = None\n"
        "        self.xs = None\n"
        "        self.plain = (0, 0)\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestOptTupleReassign:
    STR_TUPLE = (_SRC
                 + "def use(c: Cfg, auth: tuple[str, str] | None) -> Int32:\n"
                 + "    use_auth = auth\n"
                 + "    if use_auth is None:\n"
                 + "        use_auth = c.auth\n"
                 + "    if use_auth is None:\n"
                 + "        return 0\n"
                 + "    return 1\n"
                 + "def main() -> None:\n"
                 + "    print(use(Cfg(), None))\n"
                 + "main()\n")

    def test_field_source_routes_witnessed(self):
        thir, w = _lower_ctx_witnessed(self.STR_TUPLE)
        assert w.get("field.whole_optional", 0) >= 1
        out = "".join(_assert_routes_byte_identical(self.STR_TUPLE))
        assert "use_auth = c.auth;" in out
        assert ("std::optional<std::tuple<std::string, std::string>> "
                "use_auth = auth;") in out

    def test_scalar_element_tuple_routes(self):
        src = (_SRC
               + "def use(c: Cfg, p: tuple[Int32, Int32] | None) -> Int32:\n"
               + "    u = p\n"
               + "    if u is None:\n"
               + "        u = c.pair\n"
               + "    if u is None:\n"
               + "        return 0\n"
               + "    return u[0]\n"
               + "def main() -> None:\n"
               + "    print(use(Cfg(), None))\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "u = c.pair;" in out

    def test_optional_tuple_param_target_routes(self):
        # The target is a PARAM rather than a local -- same declared slot.
        src = (_SRC
               + "def use(a: tuple[Int32, Int32] | None, c: Cfg) -> Int32:\n"
               + "    if a is None:\n"
               + "        a = c.pair\n"
               + "    if a is None:\n"
               + "        return 0\n"
               + "    return 1\n"
               + "def main() -> None:\n"
               + "    print(use(None, Cfg()))\n"
               + "main()\n")
        out = "".join(_assert_routes_byte_identical(src))
        assert "a = c.pair;" in out

    def test_optional_tuple_name_source_routes(self):
        src = (_SRC
               + "def use(a: tuple[str, str] | None,\n"
               + "        b: tuple[str, str] | None) -> Int32:\n"
               + "    u = a\n"
               + "    if u is None:\n"
               + "        u = b\n"
               + "    if u is None:\n"
               + "        return 0\n"
               + "    return 1\n"
               + "def main() -> None:\n"
               + "    print(use(None, None))\n"
               + "main()\n")
        _assert_routes_byte_identical(src)


class TestOptTupleReassignBoundary:
    def test_pointer_repr_optional_container_keeps_rejecting(self):
        # BOUNDARY: an `Optional[list]` target is a POINTER slot -- its
        # whole-optional write needs the `optional_to_ptr` lift the bare
        # value-slot copy does not carry.
        src = (_SRC
               + "def use(c: Cfg, a: list[Int32] | None) -> Int32:\n"
               + "    u = a\n"
               + "    if u is None:\n"
               + "        u = c.xs\n"
               + "    if u is None:\n"
               + "        return 0\n"
               + "    return len(u)\n"
               + "def main() -> None:\n"
               + "    print(use(Cfg(), None))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.var_decl",
                           "decl.slot_type")
        _assert_byte_identical(src)

    def test_plain_tuple_target_keeps_rejecting(self):
        # BOUNDARY: a PLAIN tuple target is not the optional slot, so the
        # clamp still applies and the field read has no whole-optional row.
        src = (_SRC
               + "def use(c: Cfg, t: tuple[Int32, Int32]) -> Int32:\n"
               + "    u = t\n"
               + "    u = c.plain\n"
               + "    return u[0]\n"
               + "def main() -> None:\n"
               + "    print(use(Cfg(), (1, 2)))\n"
               + "main()\n")
        _assert_rejects_at(_fallback(src), "body:stmt.var_decl",
                           "field.result_type")
        _assert_byte_identical(src)
