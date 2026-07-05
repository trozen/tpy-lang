"""THIR `match` statements, the scalar tiers (M1-M3): the switch tiers
(switch_primitive / switch_enum -- case labels, or-patterns as stacked
labels, the wildcard `default:` arm, the synthetic `default: break;`, the
`::std::unreachable()` tail, sema-hoisted arm-declared locals, the
per-function `__match_subject_N` numbering, the break-escaping-a-switch
`goto __loop_break_N` interaction, in-switch guard chains + the
`__match_default_N` fallback), the if/elif chain tiers (unguarded `==`
chain; guarded standalone-if + `goto __match_end_N`), capture/`as`
bindings (copy/ref/assign modes), and the gate rejections (Literal
subjects, call-bearing/non-bool guards, two-binding shapes, non-name
subjects)."""

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

    def test_guard_with_call_rejects(self):
        # No flush point inside the arm block: call-bearing guards reject.
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
        assert _fn(thir, "f") is None
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

    def test_field_subpattern_rejects(self):
        # Field sub-patterns (bindings / value conditions) are deferred.
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
        assert _fn(thir, "legs") is None
        assert _cpp(src, thir=True) == _cpp(src, thir=False)

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
