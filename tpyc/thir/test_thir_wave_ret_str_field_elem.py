"""A str-family field read off a record-element CONTAINER SUBSCRIPT at a
str RETURN slot (`return self._store[k].value`).

Both halves were already admitted independently -- the field ladder's
subscript receiver row and its str result row -- but the return sink derived
the owned-str grant from its own NAME-receiver precheck, so the composition
never reached the ladder. The grant is now the RESULT row only; the receiver
still goes through the ladder, and a `String` field stays outside the
str/StrView slice. Corpus witness: `tplib.requests.CookieJar.__getitem__`."""

from .testutil import (_assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


_SRC = ("from tpy import Int32, String, StrView\n"
        "class Cookie:\n"
        "    name: str\n"
        "    view: StrView\n"
        "    owned: String\n"
        "    n: Int32\n"
        "    def __init__(self, name: str, n: Int32) -> None:\n"
        "        self.name = name\n"
        "        self.view = \"v\"\n"
        "        self.owned = String(\"o\")\n"
        "        self.n = n\n")

_JAR = (_SRC
        + "class Jar:\n"
        + "    d: dict[str, Cookie]\n"
        + "    xs: list[Cookie]\n"
        + "    def __init__(self) -> None:\n"
        + "        self.d = {}\n"
        + "        self.xs = []\n")

_MAIN = ("def main() -> None:\n"
         "    j = Jar()\n"
         "    j.d[\"a\"] = Cookie(\"a\", 1)\n"
         "    j.xs.append(Cookie(\"b\", 2))\n"
         "    print(j.get(\"a\"))\n"
         "main()\n")


def _fallback(src: str):
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules), options=CodeGenOptions(emit_source_comments=False,
                                                comment_line_numbers=False,
                                                thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestReturnStrFieldOverContainerElem:
    def test_owned_str_field_over_dict_elem_routes(self):
        src = (_JAR
               + "    def get(self, k: str) -> str:\n"
               + "        return self.d[k].name\n"
               + _MAIN)
        out = "".join(_assert_routes_byte_identical(src))
        assert "return ::tpy::__getitem__(this->d, k).name;" in out

    def test_owned_str_field_over_list_elem_routes(self):
        src = (_JAR
               + "    def get(self, k: str) -> str:\n"
               + "        return self.xs[0].name\n"
               + _MAIN)
        out = "".join(_assert_routes_byte_identical(src))
        assert "return ::tpy::__getitem__(this->xs, 0).name;" in out

    def test_view_field_fires_the_view_to_owned_copy(self):
        # A StrView member at an owned-str slot must keep the explicit
        # `std::string(...)` copy -- the half a bare admission would drop.
        # This shape routes off a view-typed result row of its own (measured:
        # it still routes with the owned-str grant removed), so the claim
        # here is the WRAP, not the grant.
        src = (_JAR
               + "    def get(self, k: str) -> str:\n"
               + "        return self.d[k].view\n"
               + _MAIN)
        out = "".join(_assert_routes_byte_identical(src))
        assert ("return std::string(::tpy::__getitem__(this->d, k).view);"
                in out)

    def test_view_field_at_a_view_slot_routes_bare(self):
        src = (_JAR
               + "    def get(self, k: str) -> StrView:\n"
               + "        return self.d[k].view\n"
               + _MAIN)
        out = "".join(_assert_routes_byte_identical(src))
        assert "return ::tpy::__getitem__(this->d, k).view;" in out


class TestReturnStrFieldOverContainerElemBoundary:
    def test_string_field_keeps_rejecting(self):
        # BOUNDARY: a `String` member resolves inside the str family but
        # outside the str/StrView slice -- its owned form has no view sink
        # to convert at, so no body arm renders it.
        src = (_JAR
               + "    def get(self, k: str) -> String:\n"
               + "        return self.d[k].owned\n"
               + _MAIN)
        _assert_rejects_at(_fallback(src), "body:stmt.return",
                           "field.result_type")
        _assert_byte_identical(src)
