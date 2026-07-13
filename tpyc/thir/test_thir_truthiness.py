"""Structured THIR truthiness wraps and their expression-position coverage."""

from __future__ import annotations

from ..codegen_cpp.context import CodeGenOptions
from .lower import lower_module
from .nodes import TruthinessMode, THIRTruthy, THIRUnaryNot
from .testutil import _compile, _entry, _fn, _lower_ctx, _lower_ctx_witnessed


_SRC = (
    "from typing import Any\n"
    "from tpy import Int32, Own\n"
    "class Flag:\n"
    "    value: bool\n"
    "    def __init__(self, value: bool):\n        self.value = value\n"
    "    def __bool__(self) -> bool:\n        return self.value\n"
    "class Counted:\n"
    "    size: Int32\n"
    "    def __init__(self, size: Int32):\n        self.size = size\n"
    "    def __len__(self) -> Int32:\n        return self.size\n"
    "class Plain:\n"
    "    value: Int32\n"
    "    def __init__(self, value: Int32):\n        self.value = value\n"
    "class Box:\n"
    "    text: str\n"
    "    def __init__(self, text: str):\n        self.text = text\n"
    "    def get(self) -> str:\n        return self.text\n"
    "class Flags:\n"
    "    first: Flag\n"
    "    second: Flag\n"
    "    def __init__(self, first: Flag, second: Flag):\n"
    "        self.first = first\n        self.second = second\n"
    "def positions(s: str, data: bytes, x: Any, flag: Flag, sized: Counted, "
    "plain: Plain) -> Int32:\n"
    "    n = 0\n"
    "    if s:\n        n += 1\n"
    "    if not data:\n        n += 2\n"
    "    assert x\n"
    "    if flag:\n        n += 4\n"
    "    while sized:\n        return n + 8\n"
    "    if plain:\n        n += 16\n"
    "    return n\n"
    "def compound(a: str, b: bytes) -> bool:\n"
    "    return True if a and b else False\n"
    "def field_method(box: Box) -> bool:\n"
    "    return True if box.text and box.get() else False\n"
    "def storage_optional(p: Own[Flag] | None) -> bool:\n"
    "    if p:\n        return True\n"
    "    return False\n"
    "def indirect(flags: Flags, choose_second: bool) -> bool:\n"
    "    selected = flags.first\n"
    "    if choose_second:\n        selected = flags.second\n"
    "    return True if selected else False\n"
    "def filtered(items: list[str]) -> Int32:\n"
    "    selected = [item for item in items if item]\n"
    "    return len(selected)\n"
)


class TestStructuredTruthiness:
    def test_all_modes_route(self):
        thir, witnessed = _lower_ctx_witnessed(_SRC)
        names = (
            "positions", "compound", "field_method", "storage_optional",
            "indirect", "filtered",
        )
        missing = [name for name in names if _fn(thir, name) is None]
        assert not missing, missing
        for mode in TruthinessMode:
            assert witnessed.get("truthy." + mode.name.lower(), 0) >= 1

    def test_nodes_carry_modes(self):
        fn = _fn(_lower_ctx(_SRC), "positions")
        first = fn.body[1].condition
        second = fn.body[2].condition
        assert isinstance(first, THIRTruthy)
        assert first.mode is TruthinessMode.NONEMPTY
        assert isinstance(second, THIRUnaryNot)
        assert isinstance(second.operand, THIRTruthy)
        assert second.operand.mode is TruthinessMode.NONEMPTY

    def test_indirect_record_dunder_dereferences_operand(self):
        fn = _fn(_lower_ctx(_SRC), "indirect")
        cond = fn.body[-1].value.cond
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.RECORD_BOOL
        assert cond.deref

    def test_thir_emit_is_byte_identical(self):
        compiler, modules = _compile(_SRC)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out

    def test_plain_record_call_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "class Plain:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32):\n        self.value = value\n"
            "def identity(p: Plain) -> Plain:\n    return p\n"
            "def probe(p: Plain) -> bool:\n"
            "    if identity(p):\n        return True\n"
            "    return False\n"
        )
        module = _entry(_compile(src)[1])
        assert _fn(lower_module(module.ast, module.analyzer), "probe") is None

    def test_narrowed_optional_name_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "class Plain:\n"
            "    value: Int32\n"
            "    def __init__(self, value: Int32):\n        self.value = value\n"
            "class Flag:\n"
            "    value: bool\n"
            "    def __init__(self, value: bool):\n        self.value = value\n"
            "    def __bool__(self) -> bool:\n        return self.value\n"
            "def probe_plain(p: Plain | None) -> Int32:\n"
            "    if p is None:\n        return 0\n"
            "    if p:\n        return p.value\n"
            "    return -1\n"
            "def probe_dunder(f: Flag | None) -> Int32:\n"
            "    if f is None:\n        return 0\n"
            "    if f:\n        return 1\n"
            "    return 2\n"
        )
        thir = _lower_ctx(src)
        assert _fn(thir, "probe_plain") is None
        assert _fn(thir, "probe_dunder") is None

    def test_indirect_record_len_dereferences_operand(self):
        src = (
            "from tpy import Int32\n"
            "class Counted:\n"
            "    size: Int32\n"
            "    def __init__(self, size: Int32):\n        self.size = size\n"
            "    def __len__(self) -> Int32:\n        return self.size\n"
            "class Pair:\n"
            "    first: Counted\n"
            "    second: Counted\n"
            "    def __init__(self, first: Counted, second: Counted):\n"
            "        self.first = first\n        self.second = second\n"
            "def probe(pair: Pair, choose_second: bool) -> bool:\n"
            "    selected = pair.first\n"
            "    if choose_second:\n        selected = pair.second\n"
            "    return True if selected else False\n"
        )
        fn = _fn(_lower_ctx(src), "probe")
        assert fn is not None
        cond = fn.body[-1].value.cond
        assert isinstance(cond, THIRTruthy)
        assert cond.mode is TruthinessMode.RECORD_LEN
        assert cond.deref
        compiler, modules = _compile(src)
        entry = _entry(modules)
        ast_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False))
        thir_out = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(
                emit_source_comments=False, thir_codegen=True))
        assert thir_out == ast_out

    def test_optional_field_narrowing_stays_ast(self):
        src = (
            "from tpy import Int32\n"
            "class Box:\n"
            "    value: Int32 | None\n"
            "    def __init__(self, value: Int32 | None):\n"
            "        self.value = value\n"
            "def probe(box: Box) -> Int32:\n"
            "    if box.value:\n        return box.value\n"
            "    return 0\n"
        )
        assert _fn(_lower_ctx(src), "probe") is None
