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
with the union tiers' field-keyword widening, the chain-optional tiers
(if_elif_optional / if_elif_optional_guarded: non-partitioned Optional
subjects, full-Optional captures, or-patterns mixing None), the str
discriminator switch (switch_str: size/char_at buckets, guarded-literal
prefix, trailing arms), and the route rejections
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


class TestMatchUnionOrBindings:
    SRC = UNION_PREAMBLE + (
        "def legs(a: Cat | Dog) -> Int32:\n"
        "    match a:\n"
        "        case Cat(legs=n) | Dog(legs=n):\n"
        "            return n\n"
        "def main() -> None:\n"
        "    print(legs(Cat()))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "legs") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        # One case block PER alternative, each with its own alias and a
        # re-lowered body; the source comment stays on the first only.
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "legs").body[0]
        assert [a.labels for a in m.arms] == [("0",), ("1",)]
        assert [a.entries[0].case_alias for a in m.arms] == [
            "__case_0_0", "__case_0_1"]
        assert m.arms[0].entries[0].loc is not None
        assert m.arms[1].entries[0].loc is None
        assert all(a.entries[0].field_bindings[0].subject_suffix == ".legs"
                   for a in m.arms)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "auto& __case_0_0 = *std::get<0>(__match_subject_1);" in cpp
        assert "auto& __case_0_1 = *std::get<1>(__match_subject_1);" in cpp
        assert cpp.count("auto n = ") == 2

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.union_or_bind", 0) > 0

    def test_guarded_or_captures(self):
        # Or-alternatives with captures + a guard reading them distribute
        # per index group on the guarded tier.
        src = UNION_PREAMBLE + (
            "def legs(a: Cat | Dog) -> Int32:\n"
            "    match a:\n"
            "        case Cat(legs=n) | Dog(legs=n) if n > 3:\n"
            "            return n\n"
            "        case Cat(legs=n) | Dog(legs=n):\n"
            "            return n + 1\n"
            "def main() -> None:\n"
            "    print(legs(Cat()))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "legs").body[0]
        assert m.strategy == "guarded_union"
        assert [a.labels for a in m.arms] == [("0",), ("1",)]
        assert all(len(a.entries) == 2 for a in m.arms)
        cpp = _cpp(src, thir=True)
        assert "if ((n > 3)) {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_guarded_or_field_cond(self):
        # A literal field condition inside an or-alternative routes the
        # match to the guarded tier and emits the per-index compare.
        src = UNION_PREAMBLE + (
            "def kind(a: Cat | Dog) -> str:\n"
            "    match a:\n"
            "        case Cat(legs=4) | Dog():\n"
            "            return \"quad-or-dog\"\n"
            "        case _:\n"
            "            return \"other\"\n"
            "def main() -> None:\n"
            "    print(kind(Cat()))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "kind").body[0]
        assert m.strategy == "guarded_union"
        cpp = _cpp(src, thir=True)
        assert "__case_0.legs == 4" in cpp
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

    def test_guarded_str_over_threshold_routes_switch_str(self):
        # A guarded str match at/over the threshold takes the discriminator
        # switch REGARDLESS of guards (_should_switch_str counts only
        # unguarded literal alternatives) -- it must NOT fall into the
        # if_elif_guarded tier.
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
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.strategy == "switch_str"
        assert len(m.str_guarded) == 1
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_str_switch_threshold_routes_switch_str(self):
        # 5+ unguarded str-literal alternatives take the discriminator
        # switch strategy, mirroring _should_switch_str.
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
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.strategy == "switch_str"
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


class TestMatchWildcardAsBinding:
    # `case _ as y` -- the always arm with a whole-subject binding
    # (`_unwrap_as_pattern` + the wildcard default on the AST path); with a
    # guard it forms the default group's in-switch guard chain.
    SRC = (
        "from tpy import Int32\n"
        "def check(x: Int32) -> str:\n"
        "    match x:\n"
        "        case _ as y if y > 10:\n"
        "            return \"big\"\n"
        "        case _:\n"
        "            return \"small\"\n"
        "    return \"\"\n"
        "def main() -> None:\n"
        "    print(check(20))\n"
        "    print(check(5))\n"
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
        assert m.strategy == "switch_primitive"
        # Both arms are always-match: one default group, guarded entry first.
        assert len(m.arms) == 1 and m.arms[0].labels == ()
        first, second = m.arms[0].entries
        assert first.binding is not None and first.binding.name == "y"
        assert first.binding.mode == "copy" and first.guard is not None
        assert second.binding is None and second.guard is None

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("check"):]
        assert "default: {" in body
        assert "auto y = __match_subject_1;" in body
        assert "if ((y > 10)) {" in body
        assert "} else {" in body

    def test_chain_tier_wildcard_as(self):
        # The `==` chain's final `} else {` arm with the binding.
        src = (
            "def label(s: str) -> str:\n"
            "    match s:\n"
            "        case \"a\":\n"
            "            return \"first\"\n"
            "        case _ as rest:\n"
            "            return rest\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(label(\"zz\"))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "label") is not None
        cpp = _cpp(src, thir=True)
        assert cpp == _cpp(src, thir=False)

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.bind_copy", 0) > 0
        assert w.get("match.wildcard_default", 0) > 0
        assert w.get("match.guard_arm", 0) > 0


class TestMatchOptionalInnerRecord:
    # The record-inner dispatch (O1, pointer-repr): None prefix, then
    # `_emit_optional_inner_record`'s if/elif chain over the deref alias --
    # a field-condition arm and a field-capture arm.
    SRC = OPT_PREAMBLE + (
        "def check(x: Leaf | None) -> str:\n"
        "    match x:\n"
        "        case None:\n"
        "            return \"none\"\n"
        "        case Leaf(n=0):\n"
        "            return \"zero\"\n"
        "        case Leaf(n=k):\n"
        "            return str(k)\n"
        "    return \"\"\n"
        "def main() -> None:\n"
        "    print(check(None))\n"
        "    print(check(Leaf(0)))\n"
        "    print(check(Leaf(7)))\n"
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
        assert m.inner_strategy == "if_elif_record"
        assert not m.optional_value_repr
        assert m.none_entry is not None
        assert len(m.arms) == 2
        assert m.arms[0].entries[0].field_conds == (("", ".n == 0"),)
        fb = m.arms[1].entries[0].field_bindings
        assert len(fb) == 1 and fb[0].name == "k"
        assert fb[0].mode == "copy" and fb[0].subject_suffix == ".n"
        # The always-match capture arm closes the chain; every arm returns.
        assert m.is_exhaustive and m.emit_unreachable

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("check"):]
        assert "if (__match_subject_1 == nullptr) {" in body
        assert "auto& __match_inner_1 = (*__match_subject_1);" in body
        assert "if (__match_inner_1.n == 0) {" in body
        assert "} else {" in body
        assert "auto k = __match_inner_1.n;" in body
        assert "::std::unreachable();" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.optional_inner_record", 0) > 0
        assert w.get("match.field_cond", 0) > 0
        assert w.get("match.field_bind", 0) > 0
        assert w.get("match.optional_none_arm", 0) > 0


class TestMatchOptionalInnerRecordOrAs:
    # Or-pattern condition groups and whole-subject `as` bindings over the
    # deref alias; the no-None form takes the bare has-value guard.
    SRC = OPT_PREAMBLE + (
        "def pick(x: Leaf | None) -> Int32:\n"
        "    match x:\n"
        "        case None:\n"
        "            return 0\n"
        "        case Leaf(n=1) | Leaf(n=2):\n"
        "            return 10\n"
        "        case Leaf() as v:\n"
        "            return v.n\n"
        "    return -1\n"
        "def main() -> None:\n"
        "    print(pick(Leaf(2)))\n"
        "    print(pick(Leaf(7)))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "pick") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "pick").body[0]
        assert isinstance(m, THIRMatch)
        assert m.inner_strategy == "if_elif_record"
        assert m.arms[0].entries[0].or_conds == (
            (("", ".n == 1"),), (("", ".n == 2"),))
        binding = m.arms[1].entries[0].binding
        assert binding is not None and binding.name == "v"
        assert binding.mode == "ref" and binding.from_case_var

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("pick"):]
        assert ("if ((__match_inner_1.n == 1) || "
                "(__match_inner_1.n == 2)) {") in body
        assert "auto& v = __match_inner_1;" in body

    def test_no_none_prefix_bare_guard(self):
        src = OPT_PREAMBLE + (
            "def grade(x: Leaf | None) -> Int32:\n"
            "    match x:\n"
            "        case Leaf(n=5):\n"
            "            return 50\n"
            "        case Leaf(n=k):\n"
            "            return k\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(grade(Leaf(5)))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "grade").body[0]
        assert m.inner_strategy == "if_elif_record"
        assert m.none_entry is None
        cpp = _cpp(src, thir=True)
        assert "if (__match_subject_1 != nullptr) {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.optional_inner_record", 0) > 0
        assert w.get("match.record_or", 0) > 0
        assert w.get("match.bind_ref", 0) > 0
        assert w.get("match.optional_inner_bind", 0) > 0


class TestMatchOptionalValueDispatch:
    SRC = (
        "from tpy import Int32\n"
        "def classify(x: Int32 | None) -> str:\n"
        "    match x:\n"
        "        case None:\n"
        "            return \"nothing\"\n"
        "        case 0:\n"
        "            return \"zero\"\n"
        "        case _:\n"
        "            return \"something\"\n"
        "    return \"\"\n"
        "def main() -> None:\n"
        "    print(classify(None))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "classify") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "classify").body[0]
        assert m.strategy == "optional_partition"
        assert m.optional_value_repr
        assert m.inner_strategy == "switch_primitive"
        assert m.none_entry is not None
        assert [a.labels for a in m.arms] == [("0",), ()]
        # A user default exists, so no synthetic `default: break;`.
        assert not m.synthetic_default

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("classify"):]
        assert "if (!__match_subject_1.has_value()) {" in body
        assert "auto& __match_inner_1 = (*__match_subject_1);" in body
        assert "switch (__match_inner_1) {" in body
        assert "default: {" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.optional_value_dispatch", 0) > 0
        assert w.get("match.optional_none_arm", 0) > 0

    def test_str_inner_if_elif_chain(self):
        # A str inner takes the `==` chain over the deref alias; no None
        # arm means the bare has_value() guard with no else.
        src = (
            "def pick(s: str | None) -> str:\n"
            "    match s:\n"
            "        case \"a\" | \"b\":\n"
            "            return \"early\"\n"
            "        case \"z\":\n"
            "            return \"late\"\n"
            "    return \"none\"\n"
            "def main() -> None:\n"
            "    print(pick(\"a\"))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "pick").body[0]
        assert m.optional_value_repr and m.inner_strategy == "if_elif"
        assert m.none_entry is None
        cpp = _cpp(src, thir=True)
        assert "if (__match_subject_1.has_value()) {" in cpp
        assert ('if (__match_inner_1 == "a" || __match_inner_1 == "b") {'
                in cpp)
        assert cpp == _cpp(src, thir=False)

    def test_enum_inner_routes(self):
        # An Optional[enum] name is inside the value-optional binding slice
        # (`_value_opt_scalar` admits registered-enum inners), so the O2
        # dispatch takes the enum inner switch.
        src = ENUM_PREAMBLE + (
            "def label(c: Color | None) -> str:\n"
            "    match c:\n"
            "        case None:\n"
            "            return \"none\"\n"
            "        case Color.Red:\n"
            "            return \"r\"\n"
            "        case _:\n"
            "            return \"other\"\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(label(Color.Red))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "label").body[0]
        assert m.strategy == "optional_partition"
        assert m.inner_strategy == "switch_enum"
        cpp = _cpp(src, thir=True)
        assert "switch (__match_inner_1) {" in cpp
        assert "case Color::Red: {" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_inner_guarded_group_default_goto(self):
        # An all-guarded labeled inner group backed by a user default draws
        # the goto __match_default_N fallback exactly like the top-level
        # switch tiers (the fact must survive into the THIRMatch node).
        src = (
            "from tpy import Int32\n"
            "def pick(x: Int32 | None, ok: bool) -> str:\n"
            "    match x:\n"
            "        case None:\n"
            "            return \"none\"\n"
            "        case 5 if ok:\n"
            "            return \"five\"\n"
            "        case _:\n"
            "            return \"other\"\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(pick(5, True))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "pick").body[0]
        assert m.default_goto
        cpp = _cpp(src, thir=True)
        assert "goto __match_default_" in cpp
        assert cpp == _cpp(src, thir=False)

    def test_str_guard_no_none_prefix_routes_chain(self):
        # A guarded arm + catch-all without a None prefix defeats the
        # partition itself, so this never reaches the O2 inner chain (whose
        # inline `cond && guard` stays unmirrored): the chain-optional
        # guarded tier takes it, guard nested inside the arm block.
        src = (
            "def pick(s: str | None, ok: bool) -> str:\n"
            "    match s:\n"
            "        case \"a\" if ok:\n"
            "            return \"guarded\"\n"
            "        case _:\n"
            "            return \"other\"\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(pick(\"a\", True))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "pick").body[0]
        assert m.strategy == "if_elif_optional_guarded"
        cpp = _cpp(src, thir=True)
        assert ("if (__match_subject_1.has_value() && "
                "(*__match_subject_1) == \"a\") {") in cpp
        assert cpp == _cpp(src, thir=False)

    def test_record_inner_rejects(self):
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
        thir = _lower_ctx(src)
        assert _fn(thir, "check") is None

    def test_non_prefix_none_routes_chain(self):
        # A trailing None arm defeats the partition; the chain-optional
        # tier takes it (mid-chain None cond, wildcard `} else {`).
        src = (
            "from tpy import Int32\n"
            "def classify(x: Int32 | None) -> str:\n"
            "    match x:\n"
            "        case 0:\n"
            "            return \"zero\"\n"
            "        case None:\n"
            "            return \"nothing\"\n"
            "        case _:\n"
            "            return \"something\"\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(classify(None))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "classify").body[0]
        assert m.strategy == "if_elif_optional"
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


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

    def test_value_repr_subject_routes(self):
        # Value-repr Optional[scalar] subjects take the O2 multi-arm
        # dispatch (see TestMatchOptionalValueDispatch).
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
        assert self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_non_prefix_none_routes_chain(self):
        # class-then-None defeats the partition: the chain-optional tier
        # takes it (`!= nullptr` class cond, `== nullptr` None cond).
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
        assert self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_value_repr_record_inner_rejects(self):
        # A ValueType-record inner (value-repr Optional) stays on the O2
        # record reject: value-repr Optional[record] names are still
        # param/decl-gated upstream, so routing the leg would ship it
        # unwitnessed.
        src = (
            "from tpy import Int32, ValueType\n"
            "class Vec(ValueType):\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def check(v: Vec | None) -> Int32:\n"
            "    match v:\n"
            "        case None:\n"
            "            return 0\n"
            "        case Vec(n=1):\n"
            "            return 10\n"
            "        case Vec(n=k):\n"
            "            return k\n"
            "    return -1\n"
            "def main() -> None:\n"
            "    print(check(Vec(1)))\n"
            "main()\n"
        )
        assert not self._routed(src, "check")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_condfree_or_alt_rejects(self):
        # A cond-free class alternative inside an or-pattern renders as a
        # literal `true` on the AST's optional-inner chain (unlike the
        # record tier's skip) -- not mirrored.
        src = OPT_PREAMBLE + (
            "def check(x: Leaf | None) -> None:\n"
            "    match x:\n"
            "        case None:\n"
            "            print(0)\n"
            "        case Leaf() | Leaf(n=2):\n"
            "            print(1)\n"
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


class TestMatchOptionalChainLiteral:
    # The unguarded chain-optional tier (no None prefix): literal arms as
    # `has_value && (*subj) == lit` conds, the wildcard as `} else {`
    # covering the None side too.
    SRC = (
        "from tpy import Int32\n"
        "def label(v: Int32 | None) -> str:\n"
        "    match v:\n"
        "        case 5:\n"
        "            return \"five\"\n"
        "        case _:\n"
        "            return \"other\"\n"
        "def main() -> None:\n"
        "    print(label(5))\n"
        "    print(label(None))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "label") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "label").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "if_elif_optional"
        assert m.optional_value_repr
        assert m.arms[0].entries[0].opt_conds == (
            (False, (("", ".has_value()"), ("(*", ") == 5"))),)
        assert m.arms[1].entries[0].opt_conds is None
        # Wildcard covers both sides; terminating arms + exhaustive.
        assert m.is_exhaustive and m.emit_unreachable

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("label"):]
        assert ("if (__match_subject_1.has_value() && "
                "(*__match_subject_1) == 5) {") in body
        assert "} else {" in body
        assert "goto" not in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.if_elif_optional", 0) > 0


class TestMatchOptionalChainOrNone:
    # An or-pattern mixing None and a literal: the null alternative renders
    # bare, the value alternative parenthesized, ||-joined.
    SRC = (
        "from tpy import Int32\n"
        "def f(v: Int32 | None) -> None:\n"
        "    match v:\n"
        "        case None | 5:\n"
        "            print(\"none-or-five\")\n"
        "        case _:\n"
        "            print(\"other\")\n"
        "def main() -> None:\n"
        "    f(None)\n"
        "    f(5)\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        assert ("if (!__match_subject_1.has_value() || "
                "(__match_subject_1.has_value() && "
                "(*__match_subject_1) == 5)) {") in cpp

    def test_or_wildcard_alt_is_always_arm(self):
        # `case None | _:` clears the condition (the wildcard alternative
        # matches everything): a bare block, legal as the only arm.
        src = (
            "from tpy import Int32\n"
            "def f(v: Int32 | None) -> None:\n"
            "    match v:\n"
            "        case None | _:\n"
            "            print(\"any\")\n"
            "def main() -> None:\n"
            "    f(1)\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        m = _fn(thir, "f").body[0]
        assert m.arms[0].entries[0].opt_conds is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.optional_chain_or", 0) > 0


class TestMatchOptionalChainGuarded:
    # The guarded chain-optional tier: standalone-if + goto __match_end_N;
    # a full-Optional capture binds before the guard that narrows it, a
    # failed guard falls through to the None arm and the wildcard.
    SRC = (
        "from tpy import Int32\n"
        "def classify(v: Int32 | None) -> None:\n"
        "    match v:\n"
        "        case x if x is not None and x > 5:\n"
        "            print(\"big\")\n"
        "        case None:\n"
        "            print(\"none\")\n"
        "        case _:\n"
        "            print(\"small\")\n"
        "def main() -> None:\n"
        "    classify(7)\n"
        "    classify(None)\n"
        "    classify(1)\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "classify") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "classify").body[0]
        assert m.strategy == "if_elif_optional_guarded"
        b = m.arms[0].entries[0].binding
        # binds_full_optional: the whole Optional, not the deref.
        assert b is not None and b.mode == "ref" and not b.from_case_var
        assert m.arms[0].entries[0].guard is not None
        assert m.arms[1].entries[0].opt_conds == (
            (False, (("!", ".has_value()"),)),)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("classify"):]
        assert "auto& x = __match_subject_1;" in body
        assert "if (((x.has_value()) && ((*x) > 5))) {" in body
        assert "goto __match_end_2;" in body
        assert "if (!__match_subject_1.has_value()) {" in body
        assert "__match_end_2:;" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.if_elif_optional_guarded", 0) > 0
        assert w.get("match.optional_full_bind", 0) > 0
        assert w.get("match.guard_arm", 0) > 0


class TestMatchOptionalChainFullCaptureAssign:
    # A lone capture arm binds the full Optional; the leaked name is
    # sema-hoisted, so the binding is the assign mode into the predecl,
    # and body reads ride the value-opt renders (None-test / deref).
    SRC = (
        "from tpy import Int32\n"
        "def full(v: Int32 | None) -> None:\n"
        "    match v:\n"
        "        case x:\n"
        "            if x is None:\n"
        "                print(\"got none\")\n"
        "            else:\n"
        "                print(x + 1)\n"
        "def main() -> None:\n"
        "    full(4)\n"
        "    full(None)\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "full") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void full"):]
        assert "std::optional<int32_t> x;" in body
        assert "x = __match_subject_1;" in body
        assert "if ((!x.has_value())) {" in body
        assert "(*x)" in body


class TestMatchOptionalChainClassThenNone:
    # Pointer-repr subject, class-then-None (partition-defeating order):
    # `!= nullptr` / `== nullptr` conds, subject reads keep the pointer
    # name (narrowed writes go through it).
    SRC = OPT_PREAMBLE + (
        "def bump(p: Leaf | None) -> Int32:\n"
        "    match p:\n"
        "        case Leaf():\n"
        "            p.n = p.n + 1\n"
        "            return p.n\n"
        "        case None:\n"
        "            return -1\n"
        "def main() -> None:\n"
        "    print(bump(Leaf(3)))\n"
        "    print(bump(None))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "bump") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("bump"):]
        assert "if (__match_subject_1 != nullptr) {" in body
        assert "} else if (__match_subject_1 == nullptr) {" in body
        assert "p->n" in body
        assert "::std::unreachable();" in body


class TestMatchOptionalChainFieldCaptureGuard:
    # A guarded class arm whose guard reads a field capture: the binding
    # emits against the `(*subj)` deref BEFORE the nested guard; a failed
    # guard falls through to the bare class arm.
    SRC = OPT_PREAMBLE + (
        "def f(p: Leaf | None) -> None:\n"
        "    match p:\n"
        "        case None:\n"
        "            print(\"none\")\n"
        "        case Leaf(n=k) if k > 10:\n"
        "            print(\"big\", k)\n"
        "        case Leaf(n=k):\n"
        "            print(\"small\", k)\n"
        "def main() -> None:\n"
        "    f(Leaf(50))\n"
        "    f(Leaf(2))\n"
        "    f(None)\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("void f"):]
        assert "auto k = (*__match_subject_1).n;" in body
        assert "if ((k > 10)) {" in body
        assert "if (__match_subject_1 == nullptr) {" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.field_bind", 0) > 0


class TestMatchOptionalChainNoneOnly:
    # Only a None arm: the partition fails (no inner cases), so the chain
    # emits the lone `if (null) { ... }` with no else (the AST shape for
    # the non-exhaustive only-None match).
    SRC = OPT_PREAMBLE + (
        "def f(p: Leaf | None) -> Int32:\n"
        "    match p:\n"
        "        case None:\n"
        "            return -1\n"
        "    return 0\n"
        "def main() -> None:\n"
        "    print(f(None))\n"
        "    print(f(Leaf(3)))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "f") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("int32_t f"):]
        assert "if (__match_subject_1 == nullptr) {" in body
        assert "else" not in body


class TestMatchOptionalChainEnum:
    # Optional[enum] subject with a trailing None arm: enum-member value
    # patterns pre-render via _enum_member_cpp; the subject read rides the
    # widened value-opt scalar slice.
    SRC = ENUM_PREAMBLE + (
        "def label(c: Color | None) -> str:\n"
        "    match c:\n"
        "        case Color.Red:\n"
        "            return \"r\"\n"
        "        case Color.Green:\n"
        "            return \"g\"\n"
        "        case Color.Blue:\n"
        "            return \"b\"\n"
        "        case None:\n"
        "            return \"none\"\n"
        "def main() -> None:\n"
        "    print(label(Color.Red))\n"
        "    print(label(None))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "label") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("label"):]
        assert ("if (__match_subject_1.has_value() && "
                "(*__match_subject_1) == Color::Red) {") in body
        assert ("} else if (!__match_subject_1.has_value()) {") in body
        assert "::std::unreachable();" in body


class TestMatchOptionalChainRejections:
    def _routed(self, src: str, name: str) -> bool:
        thir = _lower_ctx(src)
        return _fn(thir, name) is not None

    def test_ptr_full_capture_rejects(self):
        # A guarded None prefix does not cover the None side, so the
        # capture binds the full Optional -- on a pointer-repr subject
        # that is the raw `T*` binding, an unmirrored shape.
        src = OPT_PREAMBLE + (
            "def f(p: Leaf | None, flag: bool) -> None:\n"
            "    match p:\n"
            "        case None if flag:\n"
            "            print(0)\n"
            "        case v:\n"
            "            print(1)\n"
            "def main() -> None:\n"
            "    f(None, True)\n"
            "main()\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_value_opt_view_full_capture_rejects(self):
        # An Optional[str] full-Optional capture is outside the value-opt
        # SCALAR local renders (the view arg-split shim is param-only).
        src = (
            "def f(s: str | None, flag: bool) -> None:\n"
            "    match s:\n"
            "        case None if flag:\n"
            "            print(0)\n"
            "        case v:\n"
            "            print(1)\n"
            "def main() -> None:\n"
            "    f(\"a\", True)\n"
            "main()\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

    def test_or_as_binding_rejects(self):
        # An or-arm `as` binding is dropped by the AST emitters (or-arms
        # carry no bindings) -- reject, never mirror the drop.
        src = (
            "from tpy import Int32\n"
            "def f(v: Int32 | None) -> None:\n"
            "    match v:\n"
            "        case (None | 5) as y:\n"
            "            print(1)\n"
            "        case _:\n"
            "            print(2)\n"
            "def main() -> None:\n"
            "    f(5)\n"
            "main()\n"
        )
        assert not self._routed(src, "f")
        assert _cpp(src, thir=True) == _cpp(src, thir=False)


class TestMatchSwitchStrCharAt:
    # The str discriminator switch (5+ unguarded literals): char_at kind
    # wraps the switch in a size guard; per bucket the `subj == "lit"`
    # equality block + goto; wildcard trails after the switch.
    SRC = (
        "def classify(s: str) -> str:\n"
        "    match s:\n"
        "        case \"red\":\n"
        "            return \"color\"\n"
        "        case \"green\":\n"
        "            return \"color\"\n"
        "        case \"blue\":\n"
        "            return \"color\"\n"
        "        case \"cat\":\n"
        "            return \"animal\"\n"
        "        case \"dog\":\n"
        "            return \"animal\"\n"
        "        case _:\n"
        "            return \"other\"\n"
        "def main() -> None:\n"
        "    print(classify(\"red\"))\n"
        "    print(classify(\"nope\"))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "classify") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "classify").body[0]
        assert isinstance(m, THIRMatch)
        assert m.strategy == "switch_str"
        assert m.str_disc_kind == "char_at"
        assert not m.str_guarded and len(m.str_trailing) == 1
        # Buckets in ascending discriminator-value order, labels
        # pre-rendered as char literals.
        assert all(len(a.labels) == 1 for a in m.arms)

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("classify"):]
        assert "if (__match_subject_1.size() >= " in body
        assert ("switch (static_cast<unsigned char>"
                "(__match_subject_1[") in body
        assert "if (__match_subject_1 == \"red\") {" in body
        assert "goto __match_end_2;" in body
        assert "__match_end_2:;" in body
        assert "default" not in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.switch_str", 0) > 0
        assert w.get("match.str_trailing_arm", 0) > 0


class TestMatchSwitchStrLengthGuardedOr:
    # length-kind discriminator (distinct sizes), a guarded literal arm
    # emitting BEFORE the switch, an or-arm's body duplicated per
    # alternative string, and a trailing capture.
    SRC = (
        "def pick(s: str, ok: bool) -> str:\n"
        "    match s:\n"
        "        case \"a\" if ok:\n"
        "            return \"guarded\"\n"
        "        case \"a\":\n"
        "            return \"one\"\n"
        "        case \"bb\" | \"ccc\":\n"
        "            return \"mid\"\n"
        "        case \"dddd\":\n"
        "            return \"four\"\n"
        "        case \"eeeee\":\n"
        "            return \"five\"\n"
        "        case v:\n"
        "            return v\n"
        "def main() -> None:\n"
        "    print(pick(\"a\", True))\n"
        "    print(pick(\"a\", False))\n"
        "    print(pick(\"ccc\", False))\n"
        "    print(pick(\"zz\", False))\n"
        "main()\n"
    )

    def test_routed_and_byte_identical(self):
        thir = _lower_ctx(self.SRC)
        assert _fn(thir, "pick") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_node_facts(self):
        thir = _lower_ctx(self.SRC)
        m = _fn(thir, "pick").body[0]
        assert m.strategy == "switch_str"
        assert m.str_disc_kind == "length"
        assert len(m.str_guarded) == 1
        assert m.str_guarded[0].guard is not None
        assert len(m.str_trailing) == 1
        assert m.str_trailing[0].binding is not None
        # Size buckets 1..5; "bb" and "ccc" land in separate buckets, each
        # carrying the re-lowered or-arm body.
        assert [a.labels for a in m.arms] == [
            ("1",), ("2",), ("3",), ("4",), ("5",)]

    def test_emit_shape(self):
        cpp = _cpp(self.SRC, thir=True)
        body = cpp[cpp.index("pick"):]
        assert "switch (__match_subject_1.size()) {" in body
        # The guarded arm precedes the switch.
        guard_pos = body.index("if (__match_subject_1 == \"a\") {")
        assert guard_pos < body.index("switch (")
        # The or-arm body appears once per alternative bucket.
        assert body.count("return \"mid\";") == 2
        assert "auto& v = __match_subject_1;" in body

    def test_witness(self):
        _, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("match.str_guard_prefix", 0) > 0
        assert w.get("match.guard_arm", 0) > 0


class TestMatchSwitchStrRejections:
    def test_or_wildcard_alt_rejects(self):
        # An or-arm mixing a literal and a wildcard is neither a literal
        # arm nor a plain trailing wildcard/capture -- the AST's
        # CodeGenError shape, rejected.
        src = (
            "def f(s: str) -> str:\n"
            "    match s:\n"
            "        case \"a\":\n"
            "            return \"1\"\n"
            "        case \"bb\":\n"
            "            return \"2\"\n"
            "        case \"ccc\":\n"
            "            return \"3\"\n"
            "        case \"dddd\":\n"
            "            return \"4\"\n"
            "        case \"eeeee\":\n"
            "            return \"5\"\n"
            "        case \"ffffff\" | _:\n"
            "            return \"tail\"\n"
            "    return \"\"\n"
            "def main() -> None:\n"
            "    print(f(\"a\"))\n"
            "main()\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "f") is None


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


class TestMatchArmBranchDecls:
    # Arm-local branch-first decls (value and owned-record) lower inline in
    # the case block -- the branch_decls_ok admission at every match tier.
    # Distinct per-arm names: a same-named all-arm decl is sema-hoisted and
    # hits the (value-only) match hoist gate instead.
    SRC = (
        "from tpy import Int32\n"
        "class Holder:\n"
        "    val: Int32\n"
        "    def __init__(self, v: Int32) -> None:\n"
        "        self.val = v\n"
        "def pick(n: Int32) -> None:\n"
        "    match n:\n"
        "        case 2:\n"
        "            h = Holder(1)\n"
        "            k = 10\n"
        "            print(h.val + k)\n"
        "        case _:\n"
        "            g = Holder(9)\n"
        "            print(g.val)\n"
        "def main() -> None:\n"
        "    pick(2)\n"
        "    pick(5)\n"
        "main()\n"
    )

    def test_routes_and_byte_identical(self):
        assert _fn(_lower_ctx(self.SRC), "pick") is not None
        assert _cpp(self.SRC, thir=True) == _cpp(self.SRC, thir=False)

    def test_arm_decl_renders_inline(self):
        cpp = _cpp(self.SRC, thir=True)
        assert "Holder h = Holder(1);" in cpp
