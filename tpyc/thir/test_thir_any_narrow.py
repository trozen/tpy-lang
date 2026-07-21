"""D15 Any-isinstance narrowing: lowering admission, byte-identity, and the
declared-type consumer mirrors (print RAW, truthy TO_BOOL)."""
from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .testutil import _compile, _entry, _fn, _lower, _lower_ctx

_PRELUDE = "from typing import Any\nfrom tpy import Int32\n"


def _cpp(src: str, thir: bool) -> str:
    compiler, modules = _compile(src)
    entry = _entry(modules)
    hpp, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return hpp + cpp


class TestAnyNarrowLowering:
    SRC = (
        _PRELUDE
        + "def dispatch(v: Any) -> None:\n"
        + "    if isinstance(v, int):\n        print(\"int\", v)\n"
        + "    elif isinstance(v, str):\n        print(\"str\", v)\n"
        + "    else:\n        print(\"other\")\n"
        + "def tuple_form(v: Any) -> bool:\n"
        + "    if isinstance(v, (int, float)):\n        return True\n"
        + "    return False\n"
        + "def negated(v: Any) -> bool:\n"
        + "    if not isinstance(v, str):\n        return False\n"
        + "    return True\n"
    )

    def test_routing_is_non_vacuous(self):
        thir = _lower(self.SRC)
        for name in ("dispatch", "tuple_form", "negated"):
            assert _fn(thir, name) is not None, name

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emitted_shapes(self):
        out = _cpp(self.SRC, thir=True)
        assert ("if ((v.value.has_value() && "
                "v.value.type() == typeid(::tpy::BigInt))) {") in out
        assert ("const ::tpy::BigInt& __v = "
                "std::any_cast<const ::tpy::BigInt&>(v.value);") in out
        # Tuple form: one has_value guard, OR-joined typeid checks (member
        # order is the sema-canonicalized union order, mirrored from the
        # same isinstance_type members the AST arm reads).
        assert ("(v.value.has_value() && "
                "(v.value.type() == typeid(double) || "
                "v.value.type() == typeid(::tpy::BigInt)))") in out
        # Negated form: the `!` wrap around the same check.
        assert ("if ((!((v.value.has_value() && "
                "v.value.type() == typeid(std::string))))) {") in out

    def test_record_subject_routes(self):
        # The corpus record_in_any shape: alias field reads consumed inside
        # the branch (a RETURN of an alias field read gates elsewhere).
        src = (
            _PRELUDE
            + "class Rec:\n"
            + "    n: Int32\n"
            + "    def __init__(self, n: Int32):\n        self.n = n\n"
            + "def pick(v: Any) -> None:\n"
            + "    if isinstance(v, Rec):\n        print(v.n + 1)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "pick") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_global_subject_falls_back(self):
        # A module-global Any subject reads through a global slot -- not in
        # the routed slice; the body falls back whole.
        src = (
            _PRELUDE
            + "g: Any = 5\n"
            + "def f() -> bool:\n"
            + "    if isinstance(g, int):\n        return True\n"
            + "    return False\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_while_head_falls_back(self):
        # The while-isinstance-Any position is an excluded rung: the while
        # arm's _narrow_cond_info path is union-only and _lower_truthy
        # rejects the call shape, so the body falls back whole.
        src = (
            _PRELUDE
            + "def f(v: Any) -> Int32:\n"
            + "    n = 0\n"
            + "    while isinstance(v, int):\n"
            + "        n += 1\n"
            + "        if n > 3:\n            break\n"
            + "    return n\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_assert_position_falls_back(self):
        # assert-isinstance-Any is an excluded rung (no persistent
        # any_cast extraction is lowered).
        src = (
            _PRELUDE
            + "def f(v: Any) -> bool:\n"
            + "    assert isinstance(v, int)\n"
            + "    return True\n"
        )
        thir = _lower(src)
        assert _fn(thir, "f") is None

    def test_none_member_gate(self):
        # No surface spelling reaches a NoneType check member today
        # (sema rejects `type(None)` in the tuple form), so the gate is
        # pinned directly: a NoneType member must reject admission.
        from ..parse.nodes import TpyCall, TpyName
        from ..typesys import AnyType, NoneType, UnionType, INT32
        from .lower.predicates import _any_narrow_info
        cond = TpyCall(func=TpyName("isinstance"), args=[])
        cond.isinstance_var = "v"
        cond.isinstance_type = UnionType((INT32, NoneType()))
        assert _any_narrow_info(cond, {"v": AnyType()}, None) is None

    def test_declared_type_consumer_mirrors(self):
        # The AST classifies print args and truthiness by the DECLARED Any:
        # a narrowed float streams RAW (no print_float wrap) and a narrowed
        # bool truthy-tests via ::tpy::to_bool -- both mirrored.
        src = (
            _PRELUDE
            + "def f(v: Any) -> None:\n"
            + "    if isinstance(v, float):\n        print(v)\n"
            + "    elif isinstance(v, bool):\n"
            + "        if v:\n            print(\"y\")\n"
        )
        out = _cpp(src, thir=True)
        assert out == _cpp(src, thir=False)
        assert "std::cout << __v" in out
        assert "print_float" not in out
        assert "if (::tpy::to_bool(__v)) {" in out
