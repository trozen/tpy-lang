"""An owned-str tuple element written through a user record's `__setitem__`.

`self[pair[0]] = pair[1]` on `tuple[str, str]`: the element is a
`std::string` held in the tuple's own storage, so `std::get<N>(pair)` is an
owned lvalue that binds the str slot bare. A view-form NAME source still
takes the AST's view->owned copy and keeps rejecting.
"""

from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
)
from ..codegen_cpp.context import CodeGenOptions

_REC = (
    "from tpy import Int32\n"
    "class H:\n"
    "    _d: dict[str, str]\n"
    "    def __init__(self) -> None:\n"
    "        self._d = {}\n"
    "    def __setitem__(self, key: str, value: str) -> None:\n"
    "        self._d[key] = value\n"
    "    def __getitem__(self, key: str) -> str:\n"
    "        return self._d[key]\n"
)


class TestOwnedStrTupleElemThroughSelf:
    SRC = (
        _REC +
        "    def load(self, pair: tuple[str, str]) -> None:\n"
        "        self[pair[0]] = pair[1]\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.load((\"a\", \"b\"))\n"
        "    print(h[\"a\"])\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)


class TestViewNameValueKeepsRejecting:
    SRC = (
        _REC +
        "    def load(self, k: str, v: str) -> None:\n"
        "        self[k] = v\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.load(\"a\", \"b\")\n"
        "    print(h[\"a\"])\n"
        "main()\n"
    )

    def test_rejects_at_the_setitem_family(self):
        _assert_rejects_at(_reject_tally(self.SRC), 'body:stmt.assign', shape='setitem.family')


class TestOwnedStrRvalueValue:
    """An owned-str RVALUE written through the same `__setitem__`. The
    view->owned copy keys on the SOURCE being view-form; a concat, an
    f-string and a `str`-returning call are all owned `std::string`
    prvalues, so they bind the slot bare. A short-circuit chain and a
    ternary are decided recursively over their operands and stay out.
    """

    SRC = (
        _REC +
        "    def load(self, p: str) -> None:\n"
        "        self[\"concat\"] = p + \"-\" + p\n"
        "        self[\"fstr\"] = f\"{p}!\"\n"
        "        self[\"meth\"] = p.upper()\n"
        "        self[\"free\"] = str(3)\n"
        "def main() -> None:\n"
        "    h = H()\n"
        "    h.load(\"p\")\n"
        "    print(h[\"concat\"])\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)
        compiler, modules = _compile(self.SRC)
        hpp, cpp = compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        both = hpp + cpp
        assert "::tpy::__setitem__((*this), \"meth\", ::tpy::str_upper(p))" \
            in both

    def test_ternary_value_keeps_rejecting(self):
        # BOUNDARY (dualgen-probed): a ternary's form is its arms', which the
        # gate does not walk -- and a view arm would owe the owned copy.
        src = (
            _REC +
            "    def load(self, p: str, flag: bool) -> None:\n"
            "        self[\"t\"] = p if flag else p\n"
            "def main() -> None:\n"
            "    h = H()\n"
            "    h.load(\"p\", True)\n"
            "    print(h[\"t\"])\n"
            "main()\n"
        )
        _assert_rejects_at(_reject_tally(src), 'body:stmt.assign', shape='setitem.family')
