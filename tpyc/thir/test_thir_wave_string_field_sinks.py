"""Owned `String` FIELD reads at the sinks that thread `field_owned_str_ok`.

The field result row used to admit `String` only off a CALL receiver; every
threading sink renders the same bare member read and composes its own wrap
around it, so the row now takes the whole resolved str slice. Positions that
do NOT thread the flag (a container-literal ELEMENT, a membership RECEIVER)
keep rejecting -- their renders are unwitnessed, not identical-by-
construction."""

from __future__ import annotations

from .testutil import (_assert_byte_identical, _assert_routes_byte_identical,
                       _fn, _lower_ctx)

_REC = (
    "from tpy import String, Int32\n"
    "class R:\n"
    "    name: String\n"
    "    def __init__(self, n: str) -> None:\n"
    "        self.name = String(n)\n"
    "def take_view(s: str) -> Int32:\n"
    "    return len(s)\n"
    "class Sink:\n"
    "    n: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.n = 0\n"
    "    def m(self, s: str) -> None:\n"
    "        self.n += len(s)\n"
)


class TestStringFieldSinksRoute:
    def test_every_threading_sink_routes(self):
        # One body per sink: a reject in any of them would abort that body
        # only, so the routing assertion is per-sink even though the
        # fixture is one module.
        src = (_REC
               + "def s_print(r: R) -> None:\n"
               + "    print(r.name)\n"
               + "def s_fstring(r: R) -> None:\n"
               + "    print(f\"[{r.name}]\")\n"
               + "def s_compare(r: R, q: R) -> None:\n"
               + "    print(r.name == q.name)\n"
               + "def s_concat(r: R, q: R) -> None:\n"
               + "    print(r.name + q.name)\n"
               + "def s_membership_needle(r: R, hay: str) -> None:\n"
               + "    print(r.name in hay)\n"
               + "def s_call_arg(r: R) -> None:\n"
               + "    print(take_view(r.name))\n"
               + "def s_method_arg(r: R, s: Sink) -> None:\n"
               + "    s.m(r.name)\n"
               + "def s_ternary(r: R, q: R, f: bool) -> None:\n"
               + "    print(r.name if f else q.name)\n"
               + "def s_decl(r: R) -> None:\n"
               + "    x: str = r.name\n"
               + "    print(x)\n"
               + "def main() -> None:\n"
               + "    r = R(\"abc\")\n"
               + "    q = R(\"zz\")\n"
               + "    s = Sink()\n"
               + "    s_print(r)\n"
               + "    s_fstring(r)\n"
               + "    s_compare(r, q)\n"
               + "    s_concat(r, q)\n"
               + "    s_membership_needle(r, \"abcdef\")\n"
               + "    s_call_arg(r)\n"
               + "    s_method_arg(r, s)\n"
               + "    s_ternary(r, q, True)\n"
               + "    s_decl(r)\n"
               + "main()\n")
        _hpp, cpp = _assert_routes_byte_identical(src, comments=False)
        # The bare member read is the whole claim -- no view materialization
        # at the print sink.
        assert "std::cout << r.name" in cpp


class TestStringFieldNonThreadingSinksReject:
    def test_container_literal_element_stays_ast(self):
        # BOUNDARY: a container-literal ELEMENT threads the flag only for
        # the str/view element kinds -- the owned `String` element store is
        # a different render (the element slot copies), unwitnessed here.
        src = (_REC
               + "def f(r: R, q: R) -> None:\n"
               + "    items = [r.name, q.name]\n"
               + "    print(len(items))\n"
               + "def main() -> None:\n"
               + "    f(R(\"a\"), R(\"b\"))\n"
               + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)

    def test_membership_receiver_stays_ast(self):
        # BOUNDARY: only the NEEDLE side threads the flag -- the haystack
        # receiver takes the `.find()` receiver render, unwitnessed for the
        # owned member read.
        src = (_REC
               + "def f(r: R) -> None:\n"
               + "    print(\"a\" in r.name)\n"
               + "def main() -> None:\n"
               + "    f(R(\"a\"))\n"
               + "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        _assert_byte_identical(src)
