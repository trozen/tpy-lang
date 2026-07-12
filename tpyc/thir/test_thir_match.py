"""THIR `match` statements, the scalar tiers (M1-M3): the switch tiers
(switch_primitive / switch_enum -- case labels, or-patterns as stacked
labels, the wildcard `default:` arm, the synthetic `default: break;`, the
`::std::unreachable()` tail, sema-hoisted arm-declared locals, the
per-function `__match_subject_N` numbering, the break-escaping-a-switch
`goto __loop_break_N` interaction, in-switch guard chains + the
`__match_default_N` fallback), the if/elif chain tiers (unguarded `==`
chain; guarded standalone-if + `goto __match_end_N`), capture/`as`
bindings (copy/ref/assign modes), the record tiers (field conditions /
captures / or-pattern condition groups, if_elif_record + guarded_record)
with the union tiers' field-keyword widening, and the route rejections
(Literal subjects, non-bool guards, two-binding shapes,
non-name subjects, or-pattern bindings, nested field sub-patterns)."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .nodes import THIRMatch
from .testutil import _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed


def _cpp(src: str, thir: bool):
    compiler, modules = _compile(src)
    entry = _entry(modules)
    _, cpp = compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      thir_codegen=thir))
    return cpp


ENUM_PREAMBLE = (
    "from enum import Enum\n"
    "class Color(Enum):\n"
    "    Red = 0\n"
    "    Green = 1\n"
    "    Blue = 2\n"
)


class TestMatchSwitchPrimitive:
    SRC = (
        "from tpy import Int32\n"
        "def pick(n: Int32) -> None:\n"
        "    match n:\n"
        "        case 0:\n"
        "            print(10)\n"
        "        case 1:\n"
        "            print(11)\n"
        "        case _:\n"
        "            print(12)\n"
        "pick(1)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "pick") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "pick").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "switch_primitive"
        assert [a.labels for a in m.arms] == [("0",), ("1",), ()]
        assert m.subject_ref and not m.hoist_decls
        # A wildcard arm covers everything: exhaustive, no synthetic default;
        # non-terminating arms keep the unreachable tail off.
        assert m.is_exhaustive
        assert not m.synthetic_default and not m.emit_unreachable

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void pick"):]
        assert "auto& __match_subject_1 = n;" in body
        assert "switch (__match_subject_1) {" in body
        assert "case 0: {" in body and "case 1: {" in body
        assert "default: {" in body
        assert "default: break;" not in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.switch_primitive", 0) > 0
        assert w.get("match.wildcard_default", 0) > 0


class TestMatchSwitchEnumExhaustive:
    SRC = ENUM_PREAMBLE + (
        "def describe(c: Color) -> str:\n"
        "    match c:\n"
        "        case Color.Red:\n"
        "            return \"red\"\n"
        "        case Color.Green:\n"
        "            return \"green\"\n"
        "        case Color.Blue:\n"
        "            return \"blue\"\n"
        "print(describe(Color.Red))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "describe") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "describe").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "switch_enum"
        assert [a.labels for a in m.arms] == [
            ("Color::Red",), ("Color::Green",), ("Color::Blue",)]
        # Exhaustive without a wildcard: no synthetic default (-Wswitch stays
        # live for future members) and every arm returns -> unreachable tail.
        assert m.is_exhaustive and m.emit_unreachable
        assert not m.synthetic_default

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("describe"):]
        assert "case Color::Red: {" in body
        assert "default" not in body
        assert "::std::unreachable();" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.switch_enum", 0) > 0
        assert w.get("match.unreachable_tail", 0) > 0


class TestMatchOrPatternAndSyntheticDefault:
    SRC = (
        "from tpy import Int32\n"
        "def bucket(n: Int32) -> None:\n"
        "    match n:\n"
        "        case 1 | 2:\n"
        "            print(1)\n"
        "        case 3:\n"
        "            print(3)\n"
        "bucket(2)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "bucket") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "bucket").body[0]
        assert m.arms[0].labels == ("1", "2")
        assert not m.is_exhaustive and m.synthetic_default

    def test_emit_shape(self):
        # Stacked labels share one block (bare `{` line); the non-exhaustive
        # switch closes with the synthetic default.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void bucket"):]
        assert "    case 1:\n    case 2:\n    {\n" in body
        assert "default: break;" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.or_labels", 0) > 0
        assert w.get("match.synthetic_default", 0) > 0


class TestMatchNumberingAndNesting:
    SRC = (
        "from tpy import Int32\n"
        "def two(a: Int32, b: Int32) -> None:\n"
        "    match a:\n"
        "        case 0:\n"
        "            print(0)\n"
        "    match b:\n"
        "        case 1:\n"
        "            match a:\n"
        "                case 2:\n"
        "                    print(2)\n"
        "two(0, 1)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "two") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_numbering(self):
        # Pre-order per function: sequential matches then the nested one.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void two"):]
        assert "auto& __match_subject_1 = a;" in body
        assert "auto& __match_subject_2 = b;" in body
        assert "auto& __match_subject_3 = a;" in body


class TestMatchBreakInLoop:
    SRC = (
        "def scan(stop: int) -> None:\n"
        "    i = 0\n"
        "    while i < 10:\n"
        "        match i:\n"
        "            case 5:\n"
        "                break\n"
        "            case _:\n"
        "                print(i)\n"
        "        i = i + 1\n"
        "scan(5)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "scan") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        # The break sits inside the emitted switch: a bare `break;` would
        # exit the switch, so it renders as the goto with the label after
        # the loop's close brace. The arm's synthetic `break;` still follows.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void scan"):]
        assert "goto __loop_break_0;" in body
        assert "__loop_break_0:;" in body

    def test_witness(self):
        _compile(self.SRC)  # emit-side face: needs the codegen run
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          thir_codegen=True))
        assert compiler._thir_face_witnesses.get("match.loop_break_goto", 0) > 0


class TestMatchHoistDecl:
    SRC = (
        "from tpy import Int32\n"
        "def route(n: Int32) -> None:\n"
        "    match n:\n"
        "        case 0:\n"
        "            label = 10\n"
        "        case _:\n"
        "            label = 20\n"
        "    print(label)\n"
        "route(0)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "route") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "route").body[0]
        assert m.hoist_decls == (("label", "int32_t"),)

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.hoist_decl", 0) > 0


class TestMatchIfElifStr:
    SRC = (
        "def dispatch(cmd: str) -> None:\n"
        "    match cmd:\n"
        "        case \"quit\":\n"
        "            print(0)\n"
        "        case \"help\" | \"h\":\n"
        "            print(1)\n"
        "        case _:\n"
        "            print(2)\n"
        "dispatch(\"quit\")\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "dispatch") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "dispatch").body[0]
        assert m.strategy == "if_elif"
        assert [a.labels for a in m.arms] == [
            ('"quit"',), ('"help"', '"h"'), ()]
        # A chain has no switch machinery: no default, no unreachable tail
        # (arms don't terminate).
        assert not m.synthetic_default and not m.emit_unreachable

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void dispatch"):]
        assert 'if (__match_subject_1 == "quit") {' in body
        assert ('} else if ((__match_subject_1 == "help" || '
                '__match_subject_1 == "h")) {') in body
        assert "} else {" in body
        assert "switch" not in body and "break;" not in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.if_elif", 0) > 0
        assert w.get("match.if_elif_else", 0) > 0
        assert w.get("match.or_labels", 0) > 0


class TestMatchIfElifBigIntAndBool:
    INT_SRC = (
        "def pick(n: int) -> None:\n"
        "    match n:\n"
        "        case 0:\n"
        "            print(10)\n"
        "        case 1:\n"
        "            print(11)\n"
        "pick(0)\n"
    )
    BOOL_SRC = (
        "def flip(b: bool) -> None:\n"
        "    match b:\n"
        "        case True:\n"
        "            print(1)\n"
        "        case False:\n"
        "            print(0)\n"
        "flip(True)\n"
    )

    def test_bigint_routed_and_byte_identical(self):
        # `int` is BigInt: the AST's else-fallback if/elif tier, not the
        # fixed-int switch.
        thir = _lower_ctx(self.INT_SRC)
        m = _fn(thir, "pick").body[0]
        assert m.strategy == "if_elif"
        assert _cpp(self.INT_SRC, thir=True) == _cpp(self.INT_SRC, thir=False)

    def test_bool_routed_and_byte_identical(self):
        # bool stays off the switch (-Wswitch-bool): `b == true` chain.
        thir = _lower_ctx(self.BOOL_SRC)
        m = _fn(thir, "flip").body[0]
        assert m.strategy == "if_elif"
        assert [a.labels for a in m.arms] == [("true",), ("false",)]
        assert _cpp(self.BOOL_SRC, thir=True) == _cpp(self.BOOL_SRC, thir=False)


class TestMatchBindings:
    def test_switch_capture_copy(self):
        # Free-copy scalar capture (bind_by_value): `auto x = subject;` in
        # the switch's default arm.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case 0:\n"
            "            print(0)\n"
            "        case x:\n"
            "            print(x)\n"
            "f(0)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[-1].labels == () and m.arms[-1].entries[0].binding.mode == "copy"
        cpp = _cpp(src, thir=True)
        assert "auto x = __match_subject_1;" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_switch_as_binding(self):
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case 0 as z:\n"
            "            print(z)\n"
            "        case _:\n"
            "            print(1)\n"
            "f(0)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[0].labels == ("0",) and m.arms[0].entries[0].binding is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_if_elif_capture_ref(self):
        # A str capture binds by reference: `auto& other = subject;`.
        src = (
            "def f(s: str) -> None:\n"
            "    match s:\n"
            "        case \"a\":\n"
            "            print(0)\n"
            "        case other:\n"
            "            print(other)\n"
            "f(\"a\")\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[-1].entries[0].binding.mode == "ref"
        cpp = _cpp(src, thir=True)
        assert "auto& other = __match_subject_1;" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_hoisted_capture_assign(self):
        # The capture leaks past the match -> sema hoists it; the binding
        # becomes a plain assignment against the predecl slot.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case 0:\n"
            "            x = 1\n"
            "        case x:\n"
            "            print(x)\n"
            "    print(x)\n"
            "f(0)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[-1].entries[0].binding.mode == "assign"
        cpp = _cpp(src, thir=True)
        assert "x = __match_subject_1;" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_witnesses(self):
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case 0:\n"
            "            print(0)\n"
            "        case x:\n"
            "            print(x)\n"
            "f(0)\n"
        )
        _, w = _lower_ctx_witnessed(src)
        assert w.get("match.bind_copy", 0) > 0


class TestMatchIfElifGuarded:
    SRC = (
        "def greet(s: str, formal: bool) -> None:\n"
        "    match s:\n"
        "        case \"hello\" if formal:\n"
        "            print(0)\n"
        "        case \"hello\":\n"
        "            print(1)\n"
        "        case _:\n"
        "            print(2)\n"
        "greet(\"hello\", True)\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "greet") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "greet").body[0]
        assert m.strategy == "if_elif_guarded"
        assert m.arms[0].entries[0].guard is not None and m.arms[1].entries[0].guard is None
        # A duplicate literal is legal here: the guarded arm falls through.
        assert m.arms[0].labels == m.arms[1].labels == ('"hello"',)

    def test_emit_shape(self):
        # Standalone if blocks (no else-chaining), the guard nested inside,
        # goto tails, and the end label drawn as the second counter bump.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void greet"):]
        assert body.count('if (__match_subject_1 == "hello") {') == 2
        assert "} else if" not in body
        assert "if (formal) {" in body
        assert body.count("goto __match_end_2;") == 3
        assert "__match_end_2:;" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.if_elif_guarded", 0) > 0
        assert w.get("match.guard_arm", 0) > 0

    def test_guarded_capture(self):
        # The guard reads the capture bound just above it (str subject: a
        # fixed-int one would take the guarded-switch tier, still gated).
        src = (
            "def f(s: str, strict: bool) -> None:\n"
            "    match s:\n"
            "        case other if strict:\n"
            "            print(other)\n"
            "        case _:\n"
            "            print(0)\n"
            "f(\"x\", True)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        cpp = _cpp(src, thir=True)
        assert "auto& other = __match_subject_1;" in cpp
        assert "if (strict) {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_guarded_default_group(self):
        # A guarded capture + plain wildcard merge into ONE default group
        # whose entries emit as the in-switch guard chain.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case x if x > 5:\n"
            "            print(x)\n"
            "        case _:\n"
            "            print(0)\n"
            "f(9)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.strategy == "switch_primitive"
        assert len(m.arms) == 1 and len(m.arms[0].entries) == 2
        cpp = _cpp(src, thir=True)
        assert "if ((x > 5)) {" in cpp and "} else {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_guard_with_call_lowers(self):
        # A call that needs no argument temps lowers in the arm condition.
        src = (
            "def ok(v: bool) -> bool:\n"
            "    return v\n"
            "def f(s: str, v: bool) -> None:\n"
            "    match s:\n"
            "        case \"a\" if ok(v):\n"
            "            print(0)\n"
            "        case _:\n"
            "            print(1)\n"
            "f(\"a\", True)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is not None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_non_bool_guard_rejects(self):
        # The AST renders the guard raw (no truthy wrap): only bool-typed
        # guards are mirror-safe.
        src = (
            "from tpy import Int32\n"
            "def f(s: str, k: Int32) -> None:\n"
            "    match s:\n"
            "        case \"a\" if k:\n"
            "            print(0)\n"
            "        case _:\n"
            "            print(1)\n"
            "f(\"a\", 1)\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


UNION_PREAMBLE = (
    "from tpy import Int32\n"
    "class Cat:\n"
    "    legs: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.legs = 4\n"
    "class Dog:\n"
    "    legs: Int32\n"
    "    def __init__(self) -> None:\n"
    "        self.legs = 4\n"
)


class TestMatchSwitchUnion:
    SRC = UNION_PREAMBLE + (
        "def legs(a: Cat | Dog) -> Int32:\n"
        "    match a:\n"
        "        case Cat():\n"
        "            return a.legs\n"
        "        case Dog():\n"
        "            return a.legs + 1\n"
        "def main() -> None:\n"
        "    print(legs(Cat()))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "legs") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "legs").body[0]
        assert m.strategy == "switch_union" and m.is_ptr_variant
        assert [a.labels for a in m.arms] == [("0",), ("1",)]
        assert [a.entries[0].case_alias for a in m.arms] == [
            "__case_0", "__case_1"]
        assert m.is_exhaustive and m.emit_unreachable
        assert not m.synthetic_default

    def test_emit_shape(self):
        # The alias extracts the deref'd ptr alternative; body reads of the
        # subject rename to it.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("legs"):]
        assert "switch (__match_subject_1.index()) {" in body
        assert "auto& __case_0 = *std::get<0>(__match_subject_1);" in body
        assert "__case_0.legs" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.switch_union", 0) > 0
        assert w.get("match.union_alias", 0) > 0

    def test_as_binding_and_default(self):
        src = UNION_PREAMBLE + (
            "def legs(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Cat() as c:\n"
            "            return c.legs\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(legs(Dog()))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "legs").body[0]
        assert m.arms[0].entries[0].binding.from_case_var
        assert m.arms[1].labels == ()
        cpp = _cpp(src, thir=True)
        assert "auto& c = __case_0;" in cpp
        assert "default: {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_or_pattern_stacked_indices(self):
        src = UNION_PREAMBLE + (
            "class Bird:\n"
            "    legs: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.legs = 2\n"
            "def kind(a: Bird | Cat | Dog) -> str:\n"
            "    match a:\n"
            "        case Cat() | Dog():\n"
            "            return \"pet\"\n"
            "        case _:\n"
            "            return \"wild\"\n"
            "def main() -> None:\n"
            "    print(kind(Bird()))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "kind").body[0]
        assert len(m.arms[0].labels) == 2
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)

    def test_field_binding_routes(self):
        # A keyword field capture binds off the `__case_{i}` alias
        # (previously a gate reject; the record-pattern cell admitted it).
        src = UNION_PREAMBLE + (
            "def legs(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Cat(legs=n):\n"
            "            return n\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(legs(Cat()))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "legs").body[0]
        fb = m.arms[0].entries[0].field_bindings
        assert len(fb) == 1 and fb[0].subject_suffix == ".legs"
        cpp = _cpp(src, thir=True)
        assert "auto n = __case_0.legs;" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_guarded_union_routes(self):
        # M4b: per-index guard groups. Cat has a guarded + implicit
        # fallthrough; the wildcard broadcasts to Dog's index and, being
        # all-wildcard there, coalesces into default:.
        src = UNION_PREAMBLE + (
            "def legs(a: Cat | Dog, ok: bool) -> Int32:\n"
            "    match a:\n"
            "        case Cat() if ok:\n"
            "            return a.legs\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(legs(Cat(), True))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "legs").body[0]
        assert m.strategy == "guarded_union"
        # Cat's index group: guarded class entry + the broadcast wildcard.
        assert m.arms[0].labels == ("0",) and len(m.arms[0].entries) == 2
        assert m.arms[0].entries[0].guard is not None
        # Dog's index is all-wildcard -> the coalesced default group.
        assert m.arms[1].labels == ()
        cpp = _cpp(src, thir=True)
        assert "if (ok) {" in cpp
        assert "goto __match_end_2;" in cpp and "__match_end_2:;" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_guarded_union_shared_index(self):
        # Two guarded arms on ONE member share its case block as a
        # sequential guard group (the shared-variant-index route).
        src = UNION_PREAMBLE + (
            "def legs(a: Cat | Dog, ok: bool) -> Int32:\n"
            "    match a:\n"
            "        case Cat() if ok:\n"
            "            return a.legs\n"
            "        case Cat():\n"
            "            return 0\n"
            "        case Dog():\n"
            "            return 1\n"
            "def main() -> None:\n"
            "    print(legs(Cat(), True))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "legs").body[0]
        assert m.strategy == "guarded_union"
        assert len(m.arms[0].entries) == 2
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)


class TestMatchGateRejections:
    def _routed(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is not None

    def test_switch_guard_chain_routes(self):
        # Guarded + unguarded entries on one label emit the in-switch
        # guard chain inside a single case block.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32, ok: bool) -> None:\n"
            "    match n:\n"
            "        case 0 if ok:\n"
            "            print(0)\n"
            "        case 0:\n"
            "            print(2)\n"
            "        case _:\n"
            "            print(1)\n"
            "f(0, True)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert len(m.arms[0].entries) == 2
        cpp = _cpp(src, thir=True)
        assert "if (ok) {" in cpp and "} else {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_all_guarded_group_default_goto(self):
        # An all-guarded labeled group with a user default falls back via
        # goto __match_default_N onto the labeled default block.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32, ok: bool) -> None:\n"
            "    match n:\n"
            "        case 0 if ok:\n"
            "            print(0)\n"
            "        case _:\n"
            "            print(1)\n"
            "f(0, True)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.default_goto
        cpp = _cpp(src, thir=True)
        assert "goto __match_default_2;" in cpp
        assert "default: __match_default_2: {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_capture_under_as_rejects(self):
        # `case x as z:` binds TWO names to the subject -- out of the slice.
        src = (
            "from tpy import Int32\n"
            "def f(n: Int32) -> None:\n"
            "    match n:\n"
            "        case 0:\n"
            "            print(0)\n"
            "        case x as z:\n"
            "            print(x, z)\n"
            "f(0)\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_literal_subject_rejects(self):
        # A Literal[...] subject switches on the AST path, but its arms
        # carry LiteralType type_facts (dead-branch elimination) -- gated.
        src = (
            "from typing import Literal\n"
            "def f(mode: Literal[\"r\", \"w\"]) -> None:\n"
            "    match mode:\n"
            "        case \"r\":\n"
            "            print(0)\n"
            "        case \"w\":\n"
            "            print(1)\n"
            "f(\"r\")\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_guarded_str_over_threshold_rejects(self):
        # A guarded str match at/over the threshold takes the AST's
        # discriminator switch REGARDLESS of guards (_should_switch_str
        # counts only unguarded literal alternatives) -- it must NOT fall
        # into the if_elif_guarded tier.
        src = (
            "def f(s: str, ok: bool) -> None:\n"
            "    match s:\n"
            "        case \"z\" if ok:\n"
            "            print(9)\n"
            "        case \"a\":\n"
            "            print(0)\n"
            "        case \"b\":\n"
            "            print(1)\n"
            "        case \"c\":\n"
            "            print(2)\n"
            "        case \"d\":\n"
            "            print(3)\n"
            "        case \"e\":\n"
            "            print(4)\n"
            "        case _:\n"
            "            print(5)\n"
            "f(\"a\", True)\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_str_switch_threshold_rejects(self):
        # 5+ unguarded str-literal alternatives take the discriminator
        # switch strategy (deferred tier), mirroring _should_switch_str.
        src = (
            "def f(s: str) -> None:\n"
            "    match s:\n"
            "        case \"a\":\n"
            "            print(0)\n"
            "        case \"b\":\n"
            "            print(1)\n"
            "        case \"c\":\n"
            "            print(2)\n"
            "        case \"d\":\n"
            "            print(3)\n"
            "        case \"e\":\n"
            "            print(4)\n"
            "        case _:\n"
            "            print(5)\n"
            "f(\"a\")\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_non_name_subject_rejects(self):
        src = (
            "def g() -> int:\n"
            "    return 1\n"
            "def f() -> None:\n"
            "    match g():\n"
            "        case 0:\n"
            "            print(0)\n"
            "        case _:\n"
            "            print(1)\n"
            "f()\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


RECORD_PREAMBLE = (
    "from dataclasses import dataclass\n"
    "from tpy import Int32\n"
    "@dataclass\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
)


class TestMatchIfElifRecord:
    SRC = RECORD_PREAMBLE + (
        "def f(p: Point) -> Int32:\n"
        "    match p:\n"
        "        case Point(x=0, y=0):\n"
        "            return 0\n"
        "        case Point(x=x, y=0):\n"
        "            return x\n"
        "        case _:\n"
        "            return 9\n"
        "f(Point(1, 0))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "f").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "if_elif_record"
        e0, e1, e2 = (a.entries[0] for a in m.arms)
        assert e0.field_conds == (("", ".x == 0"), ("", ".y == 0"))
        assert e1.field_conds == (("", ".y == 0"),)
        assert [b.subject_suffix for b in e1.field_bindings] == [".x"]
        assert e2.field_conds == () and e2.or_conds is None
        assert m.is_exhaustive and m.emit_unreachable

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("int32_t f"):]
        assert ("if (__match_subject_1.x == 0 && __match_subject_1.y == 0) {"
                in body)
        assert "} else if (__match_subject_1.y == 0) {" in body
        assert "auto x = __match_subject_1.x;" in body
        assert "} else {" in body
        assert "::std::unreachable();" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.if_elif_record", 0) > 0
        assert w.get("match.field_cond", 0) > 0
        assert w.get("match.field_bind", 0) > 0


class TestMatchGuardedRecord:
    SRC = RECORD_PREAMBLE + (
        "def f(p: Point) -> Int32:\n"
        "    match p:\n"
        "        case Point(x=0, y=0):\n"
        "            return 0\n"
        "        case Point(x=x) if x > 0:\n"
        "            return x\n"
        "        case _:\n"
        "            return 9\n"
        "f(Point(2, 3))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "f").body[0]
        assert m.strategy == "guarded_record"
        assert m.arms[1].entries[0].guard is not None
        assert [b.subject_suffix
                for b in m.arms[1].entries[0].field_bindings] == [".x"]

    def test_emit_shape(self):
        # Standalone blocks: the guarded arm binds its capture, then nests
        # the guard; every arm tail is a goto to the second-counter label.
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("int32_t f"):]
        assert "if (__match_subject_1.x == 0 && __match_subject_1.y == 0) {" in body
        assert "auto x = __match_subject_1.x;" in body
        assert "if ((x > 0)) {" in body
        assert body.count("goto __match_end_2;") == 3
        assert "__match_end_2:;" in body
        assert "} else" not in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.guarded_record", 0) > 0
        assert w.get("match.guard_arm", 0) > 0

    def test_or_pattern_with_guard(self):
        # The or-arm's guard composes INTO the block condition (no nested
        # if), unlike class arms.
        src = RECORD_PREAMBLE + (
            "def f(p: Point, ok: bool) -> Int32:\n"
            "    match p:\n"
            "        case Point(x=0, y=0) | Point(x=1, y=1) if ok:\n"
            "            return 1\n"
            "        case _:\n"
            "            return 0\n"
            "f(Point(1, 1), True)\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[0].entries[0].or_conds == (
            (("", ".x == 0"), ("", ".y == 0")),
            (("", ".x == 1"), ("", ".y == 1")))
        cpp = _cpp(src, thir=True)
        assert ("if (((__match_subject_1.x == 0 && __match_subject_1.y == 0)"
                " || (__match_subject_1.x == 1 && __match_subject_1.y == 1))"
                " && ok) {") in cpp
        assert cpp == _cpp(src, thir=False)

    def test_or_witness(self):
        src = RECORD_PREAMBLE + (
            "def f(p: Point) -> Int32:\n"
            "    match p:\n"
            "        case Point(x=0, y=0) | Point(x=1, y=1):\n"
            "            return 1\n"
            "        case _:\n"
            "            return 0\n"
            "f(Point(1, 1))\n"
        )
        _, w = _lower_ctx_witnessed(src)
        assert w.get("match.record_or", 0) > 0
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestMatchRecordFieldNone:
    SRC = (
        "class W:\n"
        "    opt: \"str | None\"\n"
        "    uni: \"int | str | None\"\n"
        "    def __init__(self, opt: \"str | None\","
        " uni: \"int | str | None\") -> None:\n"
        "        self.opt = opt\n"
        "        self.uni = uni\n"
        "def f(w: W) -> Int32:\n"
        "    match w:\n"
        "        case W(opt=None):\n"
        "            return 1\n"
        "        case W(uni=None):\n"
        "            return 2\n"
        "        case _:\n"
        "            return 3\n"
        "from tpy import Int32\n"
        "f(W(None, 1))\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts_and_emit(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "f").body[0]
        assert m.arms[0].entries[0].field_conds == (
            ("!", ".opt.has_value()"),)
        assert m.arms[1].entries[0].field_conds == (
            ("std::holds_alternative<std::monostate>(", ".uni)"),)
        cpp = _cpp(self.SRC, thir=True)
        assert "if (!__match_subject_1.opt.has_value()) {" in cpp
        assert ("} else if (std::holds_alternative<std::monostate>"
                "(__match_subject_1.uni)) {") in cpp

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.field_none", 0) > 0


class TestMatchGuardedUnionFieldCond:
    SRC = UNION_PREAMBLE + (
        "def f(a: Cat | Dog) -> Int32:\n"
        "    match a:\n"
        "        case Dog(legs=3):\n"
        "            return 3\n"
        "        case _:\n"
        "            return 0\n"
        "def main() -> None:\n"
        "    print(f(Dog()))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "f").body[0]
        assert m.strategy == "guarded_union"
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts_and_emit(self):
        # A field condition routes the union to the guarded path; the
        # condition pre-renders against the group's variant-index alias
        # and the wildcard broadcast keeps the Dog group two entries.
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "f").body[0]
        dog = m.arms[0]
        assert dog.labels == ("1",) and len(dog.entries) == 2
        assert dog.entries[0].field_conds == (("", ".legs == 3"),)
        cpp = _cpp(self.SRC, thir=True)
        assert "auto& __case_1 = *std::get<1>(__match_subject_1);" in cpp
        assert "if (__case_1.legs == 3) {" in cpp
        assert "__match_end_2:;" in cpp

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.union_field_cond", 0) > 0
        assert w.get("match.guarded_union", 0) > 0

    def test_cond_with_guard_composes(self):
        # `if (cond && guard)` -- field conds first, then the guard.
        src = UNION_PREAMBLE + (
            "def f(a: Cat | Dog, ok: bool) -> Int32:\n"
            "    match a:\n"
            "        case Dog(legs=3) if ok:\n"
            "            return 3\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(f(Dog(), True))\n"
            "main()\n"
        )
        cpp = _cpp(src, thir=True)
        assert "if (__case_1.legs == 3 && ok) {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_cond_and_binding_binds_inside(self):
        # With field conds the bindings move INSIDE the condition block
        # (the AST declares them after the check).
        src = UNION_PREAMBLE + (
            "def f(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Dog(legs=4) as d:\n"
            "            return d.legs\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(f(Dog()))\n"
            "main()\n"
        )
        cpp = _cpp(src, thir=True)
        i_cond = cpp.index("if (__case_1.legs == 4) {")
        i_bind = cpp.index("auto& d = __case_1;")
        assert i_cond < i_bind
        assert cpp == _cpp(src, thir=False)


class TestMatchRecordRejections:
    def _routed(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is not None

    def test_as_over_or_rejects(self):
        # The AST record or-branches never emit the `as` binding
        # (ill-formed C++ when the body reads it -- BUGS.md).
        src = RECORD_PREAMBLE + (
            "def f(p: Point) -> Int32:\n"
            "    match p:\n"
            "        case Point(x=0) | Point(y=0) as q:\n"
            "            return q.x\n"
            "        case _:\n"
            "            return 0\n"
        )
        assert not self._routed(src, "f")

    def test_or_alt_capture_rejects(self):
        # Field captures inside or-alternatives are silently dropped by
        # the AST or-branches (ill-formed C++ -- BUGS.md).
        src = RECORD_PREAMBLE + (
            "def f(p: Point) -> Int32:\n"
            "    match p:\n"
            "        case Point(x=a, y=0) | Point(x=0, y=a):\n"
            "            return a\n"
            "        case _:\n"
            "            return 0\n"
        )
        assert not self._routed(src, "f")

    def test_union_field_guard_subpattern_rejects(self):
        # A class sub-pattern on a union-typed field (holds_alternative +
        # std::get extraction) is a deferred row.
        src = (
            "from tpy import Int32\n"
            "class A:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 1\n"
            "class B:\n"
            "    n: Int32\n"
            "    def __init__(self) -> None:\n"
            "        self.n = 2\n"
            "class W:\n"
            "    v: A | B\n"
            "    def __init__(self, v: A | B) -> None:\n"
            "        self.v = v\n"
            "def f(w: W) -> Int32:\n"
            "    match w:\n"
            "        case W(v=A()):\n"
            "            return 1\n"
            "        case _:\n"
            "            return 0\n"
            "def main() -> None:\n"
            "    print(f(W(A())))\n"
            "main()\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_field_as_subpattern_rejects(self):
        # `field=(<pat> as v)` sub-patterns are a deferred row. (No arm may
        # follow: sema counts the as-wrapped literal as non-constraining and
        # flags any later arm unreachable.)
        src = RECORD_PREAMBLE + (
            "def f(p: Point) -> Int32:\n"
            "    match p:\n"
            "        case Point(x=(0 as v)):\n"
            "            return v\n"
            "    return 9\n"
            "f(Point(0, 1))\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_guarded_union_guard_reads_cond_capture_rejects(self):
        # With field conds the AST composes `if (conds && guard)` and only
        # then declares the captures inside the block, so a guard reading
        # its own capture is ill-formed C++ (BUGS.md) -- gate-rejected.
        src = UNION_PREAMBLE + (
            "def f(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Dog(legs=4, name=n) if n > 2:\n"
            "            return n\n"
            "        case _:\n"
            "            return 0\n"
        )
        src = src.replace("class Dog:\n    legs: Int32\n",
                          "class Dog:\n    legs: Int32\n    name: Int32\n")
        src = src.replace("    def __init__(self) -> None:\n"
                          "        self.legs = 4\nclass Cat",
                          "    def __init__(self) -> None:\n"
                          "        self.legs = 4\n        self.name = 4\n"
                          "class Cat")
        assert not self._routed(src, "f")

    def test_guarded_union_guard_reads_subject_rejects(self):
        # The AST renders the guard BEFORE the arm's narrowing applies, so
        # a guard reading the SUBJECT would spell the raw variant name --
        # rejected inline via `forbidden_reads`.
        src = UNION_PREAMBLE + (
            "def f(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Dog() if a.legs > 2:\n"
            "            return 1\n"
            "        case _:\n"
            "            return 0\n"
            "f(Dog())\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_hoisted_record_as_capture_rejects(self):
        # A leaked record `as` capture hoists in pointer form on the AST
        # (`Point* q;`) -- outside the plain-value hoist slice.
        src = RECORD_PREAMBLE + (
            "def f(p: Point) -> Int32:\n"
            "    match p:\n"
            "        case Point() as q:\n"
            "            pass\n"
            "    return q.x\n"
            "f(Point(1, 2))\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


OPT_PREAMBLE = (
    "from tpy import Int32\n"
    "class Leaf:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n"
    "        self.n = n\n"
)


class TestMatchOptionalPartition:
    SRC = OPT_PREAMBLE + (
        "def check(x: Leaf | None) -> None:\n"
        "    match x:\n"
        "        case None:\n"
        "            print(0)\n"
        "        case Leaf():\n"
        "            print(x.n)\n"
        "def main() -> None:\n"
        "    check(Leaf(3))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "check") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "check").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "optional_partition"
        assert m.none_entry is not None and m.none_entry.binding is None
        assert len(m.arms) == 1 and m.arms[0].labels == ()
        assert m.arms[0].entries[0].binding is None
        # None + class arm covers both sides; non-terminating bodies keep
        # the unreachable tail off.
        assert m.is_exhaustive and not m.emit_unreachable
        assert not m.synthetic_default and not m.default_goto

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void check"):]
        assert "auto& __match_subject_1 = x;" in body
        assert "if (__match_subject_1 == nullptr) {" in body
        assert "auto& __match_inner_1 = (*__match_subject_1);" in body
        # Subject reads in the narrowed arm keep the pointer name.
        assert "x->n" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.optional_partition", 0) > 0
        assert w.get("match.optional_none_arm", 0) > 0


class TestMatchOptionalPartitionBindings:
    def test_capture_binds_inner_ref(self):
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case v:\n"
            "            print(v.n)\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "check").body[0]
        b = m.arms[0].entries[0].binding
        assert b is not None and b.mode == "ref" and b.from_case_var
        cpp = _cpp(src, thir=True)
        assert "auto& v = __match_inner_1;" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        _, w = _lower_ctx_witnessed(src)
        assert w.get("match.optional_inner_bind", 0) > 0
        assert w.get("match.bind_ref", 0) > 0

    def test_as_binding_and_terminating_tail(self):
        src = OPT_PREAMBLE + (
            "def pick(x: Leaf | None) -> Int32:\n"
            "    match x:\n"
            "        case None:\n"
            "            return 0\n"
            "        case Leaf() as v:\n"
            "            return v.n\n"
            "def main() -> None:\n"
            "    print(pick(Leaf(3)))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "pick").body[0]
        assert m.arms[0].entries[0].binding.name == "v"
        assert m.is_exhaustive and m.emit_unreachable
        cpp = _cpp(src, thir=True)
        assert "::std::unreachable();" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_no_none_arm_has_value_guard(self):
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case Leaf():\n"
            "            print(x.n)\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "check").body[0]
        assert m.strategy == "optional_partition" and m.none_entry is None
        cpp = _cpp(src, thir=True)
        assert "if (__match_subject_1 != nullptr) {" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)
        _, w = _lower_ctx_witnessed(src)
        assert w.get("match.optional_value_only", 0) > 0

    def test_nested_in_none_body_counter_draws(self):
        # A nested match in the None body draws counter 2; the outer inner
        # alias must keep the OUTER draw (snapshot before the body emits).
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None, y: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            match y:\n"
            "                case None:\n"
            "                    print(0)\n"
            "                case v:\n"
            "                    print(v.n)\n"
            "        case v:\n"
            "            print(v.n)\n"
            "def main() -> None:\n"
            "    check(Leaf(3), None)\n"
            "main()\n"
        )
        cpp = _cpp(src, thir=True)
        assert "auto& __match_inner_2 = (*__match_subject_2);" in cpp
        assert "auto& __match_inner_1 = (*__match_subject_1);" in cpp
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestMatchOptionalGateRejections:
    def _routed(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is not None

    def test_value_repr_subject_rejects(self):
        # A value-repr Optional subject's std::optional param binding is
        # itself function-gated; nothing routes.
        src = (
            "from tpy import Int32\n"
            "def check(x: Int32 | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case 5:\n"
            "            print(1)\n"
            "        case _:\n"
            "            print(2)\n"
            "def main() -> None:\n"
            "    check(5)\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_non_prefix_none_rejects(self):
        # class-then-None defeats the partition: the AST takes the
        # if/elif-optional tier (deferred).
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case Leaf():\n"
            "            print(x.n)\n"
            "        case None:\n"
            "            print(0)\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_keyword_class_pattern_rejects(self):
        # Field conditions are the record-pattern tier.
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case Leaf(n=3):\n"
            "            print(1)\n"
            "        case _:\n"
            "            print(2)\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_guarded_inner_arm_rejects(self):
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None, ok: bool) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case Leaf() if ok:\n"
            "            print(1)\n"
            "        case _:\n"
            "            print(2)\n"
            "def main() -> None:\n"
            "    check(Leaf(3), True)\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_capture_write_rejects(self):
        # Mutation through the capture is ill-formed on the AST path when
        # the subject is const (BUGS.md: capture-alias mutations never
        # reach the subject's const verdict) -- gate-rejected, not
        # mirrored.
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case v:\n"
            "            v.n += 1\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        assert not self._routed(src, "check")

    def test_full_optional_capture_rejects(self):
        # No None prefix + a catchall: the partition itself fails (the
        # capture would bind the full Optional).
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case v:\n"
            "            print(1)\n"
            "def main() -> None:\n"
            "    check(Leaf(3))\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestMatchRecordOrWildcardAlt:
    # A record-subject or-pattern with a wildcard alternative clears the
    # rendered condition list (or_conds == ()), taking the always-match
    # emit branch of the record tiers -- legal only as the final arm.
    SRC = RECORD_PREAMBLE + (
        "def f(p: Point) -> Int32:\n"
        "    match p:\n"
        "        case Point(x=1, y=0):\n"
        "            return 5\n"
        "        case Point(x=0, y=0) | _:\n"
        "            return 1\n"
        "    return 9\n"
        "def main():\n"
        "    print(f(Point(1, 0)))\n    print(f(Point(2, 3)))\n"
        "main()\n"
    )

    def test_routes(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None

    def test_byte_identical(self):
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)
