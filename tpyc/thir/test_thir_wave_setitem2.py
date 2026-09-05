"""Long-tail setitem/value rows: the owned-bytes value slot's
`bytes_copy` materialize, the whole Optional[str] value store (the ARG
split with the consuming move wrap), the len() over an owned bytes/str
container element, the open-K generic dict FIELD write, the plain
RECORD-element tuple value's `tuple_to_storage` call lift, and the
dict-value tuple-element field write (`d[1][0].n = 24`)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _lower_ctx, _lower_ctx_witnessed, _fn, _assert_byte_identical,
)


class TestSetitemValueRows:
    def test_bytes_value_slot_copies(self):
        src = ("def store(a: bytes | None) -> None:\n"
               "    out: dict[str, bytes] = {}\n"
               "    if a is not None:\n"
               "        out[\"k\"] = a\n"
               "    print(len(out), len(out[\"k\"]) if a is not None"
               " else 0)\n"
               "def main() -> None:\n"
               "    store(b\"hi\")\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "store") is not None
        assert faces["setitem.bytes_owned_copy"] >= 1
        cpp = _assert_byte_identical(src)
        assert ("::tpy::__setitem__(out, \"k\", ::tpy::bytes_copy((*a)));"
                in cpp[1])
        assert ("::tpy::__len__(::tpy::__getitem__(out, \"k\"))" in cpp[1])

    def test_optional_str_whole_store(self):
        src = ("def store(a: str | None) -> None:\n"
               "    out: dict[str, str | None] = {}\n"
               "    out[\"present\"] = a\n"
               "    out[\"absent\"] = None\n"
               "    print(len(out))\n"
               "def main() -> None:\n"
               "    store(\"x\")\n"
               "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "store") is not None
        assert faces["setitem.optview_whole"] >= 2
        cpp = _assert_byte_identical(src)
        assert ("::tpy::__setitem__(out, \"present\", std::move(a ? "
                "std::make_optional(std::string(*a)) : std::nullopt));"
                in cpp[1])

    def test_narrowed_source_at_optional_slot_stays_ast(self):
        # The NARROWED occurrence renders the deref-spelled condition
        # (`std::move((*a ? ...))`), unmirrored -- the occurrence-typed
        # guard keeps it on the AST path.
        src = ("def store(a: str | None) -> None:\n"
               "    out: dict[str, str | None] = {}\n"
               "    if a is not None:\n"
               "        out[\"k\"] = a\n"
               "    print(len(out))\n"
               "def main() -> None:\n"
               "    store(\"x\")\n"
               "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.optview_value_shape")

    def test_generic_dict_field_write_routes(self):
        src = ("from tpy import Int32\n"
               "class WithDict[K, V]:\n"
               "    data: dict[K, V]\n"
               "    def __init__(self):\n"
               "        self.data = dict()\n"
               "def main() -> None:\n"
               "    d = WithDict[str, Int32]()\n"
               "    d.data[\"x\"] = Int32(99)\n"
               "    print(\"dict:\", d.data)\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        cpp = _assert_byte_identical(src)
        assert "::tpy::__setitem__(d.data, \"x\", 99);" in cpp[1]


class TestSetitemBoundaries:
    def test_generic_dict_record_value_routes(self):
        # The open-K admission composes with the F1-record elem row for a
        # RECORD value -- routes byte-identically (the family gate still
        # decides per element).
        src = ("from tpy import Int32\n"
               "class WithDict[K, V]:\n"
               "    data: dict[K, V]\n"
               "    def __init__(self):\n"
               "        self.data = dict()\n"
               "class Blob:\n"
               "    n: Int32\n"
               "    def __init__(self, n: Int32) -> None:\n"
               "        self.n = n\n"
               "def main() -> None:\n"
               "    d = WithDict[str, Blob]()\n"
               "    d.data[\"x\"] = Blob(1)\n"
               "    print(len(d.data))\n"
               "main()\n")
        thir = _lower_ctx(src)
        assert _fn(thir, "main") is not None
        _assert_byte_identical(src)

class TestRecordTupleValue:
    _BOX = (
        "from tpy import Int32\n"
        "class Box:\n"
        "    n: Int32\n"
        "    def __init__(self, n: Int32) -> None:\n"
        "        self.n = n\n"
        "def make_pair(b: Box) -> tuple[Box, Box]:\n"
        "    return (b, b)\n"
    )

    def test_call_value_takes_the_storage_lift(self):
        src = self._BOX + (
            "def f(b: Box) -> None:\n"
            "    d: dict[Int32, tuple[Box, Box]] = {}\n"
            "    d[1] = make_pair(b)"
            "  # tpyc: warning(/copies Box into container/)"
            " warning(/copies Box into container/)\n"
            "    d[1][0].n = 24\n"
            "    print(d[1][0].n, b.n)\n"
            "def main() -> None:\n"
            "    f(Box(1))\n"
            "main()\n")
        thir, faces = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert faces["setitem.record_tuple_call"] >= 1
        cpp = _assert_byte_identical(src)
        assert ("::tpy::__setitem__(d, 1, ::tpy::tuple_to_storage"
                "<std::tuple<Box, Box>>(make_pair(b)));" in cpp[1])
        assert "std::get<0>(::tpy::__getitem__(d, 1)).n = 24;" in cpp[1]

    def test_tuple_literal_value_stays_ast(self):
        # Only the CALL source is witnessed; a record-tuple LITERAL value
        # keeps its per-element lift render on the AST path.
        src = self._BOX + (
            "def f(a: Box, b: Box) -> None:\n"
            "    d: dict[Int32, tuple[Box, Box]] = {}\n"
            "    d[1] = (a, b)"
            "  # tpyc: warning(/copies Box into container/)"
            " warning(/copies Box into container/)\n"
            "    print(d[1][0].n)\n"
            "def main() -> None:\n"
            "    f(Box(1), Box(2))\n"
            "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:stmt.assign:setitem.btuple_value_shape")
