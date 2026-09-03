"""THIR NoneType (`std::monostate`) value slots: the unit type at the four
value-bearing positions -- free-call arg, annotated local decl, field write,
and ctor member-init. One semantic axis (borrow and storage coincide;
`None` literal renders the STORAGE `std::monostate{}`), four independent
admission cascades. The return slot is the carve-out (`-> None` is
VoidType -> `void`) and a NoneType NAME at an arg slot keeps rejecting
(`_none_unit_arg` is literal-only)."""

from __future__ import annotations

from .testutil import (
    _assert_rejects_at,
    _reject_tally, _assert_byte_identical, _fn, _lower_ctx,
                       _lower_ctx_witnessed, _assert_routes_byte_identical)

SRC = (
    "class Field:\n"
    "    slot: None\n"
    "    def __init__(self):\n"
    "        self.slot = None\n"
    "    def take(self, x: None) -> None:\n"
    "        self.slot = x\n"
    "def takes_none(x: None) -> None:\n"
    "    local: None = x\n"
    "    print(\"ran\")\n"
    "def main() -> None:\n"
    "    f = Field()\n"
    "    f.take(None)\n"
    "    takes_none(None)\n"
    "main()\n"
)


class TestNoneUnitSlots:
    def test_routed_and_witnessed(self):
        thir, witnessed = _lower_ctx_witnessed(SRC)
        for name in ("takes_none", "main"):
            assert _fn(thir, name) is not None, name
        assert witnessed.get("call.none_unit", 0) >= 2   # f.take + free call
        assert witnessed.get("decl.none_unit_slot", 0) >= 1
        assert witnessed.get("field.none_unit_write", 0) >= 1

    def test_byte_identical_renders(self):
        hpp, cpp = _assert_routes_byte_identical(SRC)
        assert "takes_none(std::monostate{});" in cpp
        assert "std::monostate local = x;" in cpp
        assert "this->slot = x;" in hpp + cpp
        assert "Field::Field() : slot(std::monostate{}) {}" in hpp + cpp

    def test_ctor_mil_witnessed(self):
        # Ctor MIL lowering runs during code generation (not lower_module),
        # so the witness is read off the compiler after a full THIR emit.
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(SRC)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert compiler._thir_face_witnesses.get("mil.none_unit", 0) >= 1

    def test_none_name_arg_stays_ast(self):
        # BOUNDARY: a NoneType-typed NAME at an arg slot keeps rejecting --
        # `_none_unit_arg` is literal-only and the corpus has no name-shape
        # witness.
        src = (
            "def takes_none(x: None) -> None:\n"
            "    pass\n"
            "def relay(x: None) -> None:\n"
            "    takes_none(x)\n"
        )
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.arg_shape.other_nonetype")

    def test_optional_none_arg_not_claimed(self):
        # BOUNDARY: `None` at a value-repr Optional slot stays on the
        # `std::nullopt` arm -- the unit-slot row must not claim it.
        src = (
            "from tpy import Int32\n"
            "def takes_opt(x: Int32 | None) -> None:\n"
            "    pass\n"
            "def f() -> None:\n"
            "    takes_opt(None)\n"
            "f()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert not witnessed.get("call.none_unit")
        _hpp, cpp = _assert_routes_byte_identical(src)
        assert "takes_opt(std::nullopt);" in cpp

    def test_optional_slots_not_claimed_by_unit_rows(self):
        # BOUNDARY for the decl / field-write / MIL rows: `None` at an
        # OPTIONAL slot belongs to the std::nullopt arms -- none of the
        # unit-row faces may claim it.
        src = (
            "from tpy import Int32\n"
            "class H:\n"
            "    slot: Int32 | None\n"
            "    def __init__(self):\n"
            "        self.slot = None\n"
            "    def clear(self) -> None:\n"
            "        self.slot = None\n"
            "def f() -> None:\n"
            "    local: Int32 | None = None\n"
            "    h = H()\n"
            "    h.clear()\n"
            "    print(local is None)\n"
            "f()\n"
        )
        thir, witnessed = _lower_ctx_witnessed(src)
        assert _fn(thir, "f") is not None
        assert not witnessed.get("decl.none_unit_slot")
        assert not witnessed.get("field.none_unit_write")
        from ..codegen_cpp.context import CodeGenOptions
        from .testutil import _compile, _entry
        compiler, modules = _compile(src)
        compiler.generate_code_to_strings(
            _entry(modules),
            options=CodeGenOptions(emit_source_comments=False))
        assert not compiler._thir_face_witnesses.get("mil.none_unit")
        _assert_byte_identical(src)

    def test_none_literal_field_write_in_method(self):
        # The method-body sibling of the ctor MIL: `self.slot = None`
        # outside __init__ takes the residual field-write family's STORAGE
        # literal (`this->slot = std::monostate{};`).
        src = (
            "class Field:\n"
            "    slot: None\n"
            "    def __init__(self):\n"
            "        self.slot = None\n"
            "    def reset(self) -> None:\n"
            "        self.slot = None\n"
            "def main() -> None:\n"
            "    f = Field()\n"
            "    f.reset()\n"
            "main()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "this->slot = std::monostate{};" in hpp + cpp
