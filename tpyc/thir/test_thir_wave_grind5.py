"""Wave 5 of the grind loop: the value-union ctor-arg temp chain at DECL
sinks (the nested-type-pattern pair's blocker stack).

The AST hoists a member ctor's value-union arg temp BEFORE the decl's slot
line (`std::variant<int32_t, std::string> __tmp_1 = "hi";` then
`std::variant<...> __slot_N = Container(__tmp_1);`). Five pieces landed:
the union ptr-slot / opt-ptr-slot decl inits thread allow_temps; the
whole-union coerced-literal slot kind; generic member ctors at the
ptr-union arg-temp arm; container-literal elements inherit the enclosing
flush right; the emit's ptr-decl arms flush pended temps before their slot
lines (with the validator granting the position).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_CONTAINER = (
    "from tpy import Int32\n"
    "class Container:\n"
    "    value: str | Int32\n"
    "    def __init__(self, value: str | Int32) -> None:\n"
    "        self.value = value\n"
)


class TestUnionSlotDeclCtorArgTemp:
    SRC = (
        _CONTAINER +
        "def read(x: Int32 | Container) -> Int32:\n"
        "    if isinstance(x, Int32):\n"
        "        return x\n"
        "    return 0\n"
        "def main() -> None:\n"
        "    b1: Int32 | Container = Container(\"world\")\n"
        "    b3: Int32 | Container = 99\n"
        "    print(read(b1), read(b3))\n"
        "main()\n"
    )

    def test_member_ctor_and_literal_union_decls_route(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        # The arg temp prints BEFORE the slot line (the AST drain order).
        i_tmp = cpp.index('std::variant<int32_t, std::string> __tmp_1 = "world";')
        i_slot = cpp.index("__slot_1 = Container(__tmp_1);")
        assert i_tmp < i_slot
        assert "__slot_2 = 99;" in cpp

    def test_ambiguous_int_member_literal_keeps_rejecting(self):
        # TWO int-family members: the variant's converting ctor would be
        # ambiguous for a bare literal, so the whole-union literal arm
        # rejects (the single-member rule, shared with the arg-temp rows).
        src = (
            "from tpy import Int32, Int64\n"
            + _CONTAINER.replace("str | Int32", "str | Int32") +
            "def main() -> None:\n"
            "    b: Int32 | Int64 | Container = 99\n"
            "    print(1)\n"
            "main()\n"
        )
        fell = _thir_fallbacks(src)
        assert any(k.startswith("body:") for k in fell), fell

    def test_ternary_arm_ctor_keeps_rejecting(self):
        # A ternary ARM is not a flush position (the arm may never run, so
        # its temp must not hoist eagerly) -- the union-arg ctor inside one
        # stays on the AST path.
        src = (
            _CONTAINER +
            "def pick(c: bool) -> None:\n"
            "    x: Int32 | Container = "
            "Container(\"a\") if c else Container(\"b\")\n"
            "    print(1)\n"
        )
        fell = _thir_fallbacks(src)
        assert any(k.startswith("body:") for k in fell), fell


class TestGenericMemberCtorAtUnionSlot:
    SRC = (
        "from tpy import Int32, Own\n"
        "class Box[T]:\n"
        "    value: T\n"
        "    def __init__(self, value: T) -> None:\n"
        "        self.value = value\n"
        "class Outer:\n"
        "    item: Box[str] | Box[Int32]\n"
        "    def __init__(self, item: Box[str] | Box[Int32]) -> None:\n"
        "        self.item = item\n"
        "def main() -> None:\n"
        "    c1 = Outer(Box(\"abc\"))\n"
        "    print(1)\n"
        "main()\n"
    )

    def test_inferred_instantiation_hoists_named_temp(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert 'Box<std::string> __tmp_1 = Box<std::string>("abc");' in cpp
        assert "{&__tmp_1}" in cpp


class TestContainerLiteralElemCtorArgTemp:
    SRC = (
        "from tpy import Int32\n"
        "class Tagged:\n"
        "    tag: str\n"
        "    value: Int32 | str\n"
        "    def __init__(self, tag: str, value: Int32 | str) -> None:\n"
        "        self.tag = tag\n"
        "        self.value = value\n"
        "def main() -> None:\n"
        "    items: list[Tagged] = [\n"
        "        Tagged(\"s\", \"hi\"),\n"
        "        Tagged(\"n\", Int32(5)),\n"
        "    ]\n"
        "    print(len(items))\n"
        "main()\n"
    )

    def test_element_ctor_union_args_hoist_at_decl(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        i_tmp = cpp.index('__tmp_1 = "hi";')
        i_lit = cpp.index('{Tagged("s", __tmp_1), Tagged("n", __tmp_2)}')
        assert i_tmp < i_lit
