"""Wave 7 of the grind loop: the owned-optional move-out stack (four
links, one case).

- A ptr-repr Optional NAME at an `Optional[Own[record]]` BY-VALUE slot
  lifts owning storage at its movable last use
  (`Boxed(tmp)` -> `Boxed(::tpy::ptr_to_optional_move(tmp))`).
- An Own-optional-returning FREE call lands bare in its storage
  `std::optional<T>` decl slot (the method gate's escape, free twin).
- A NARROWED owned-optional record NAME receiver derefs
  (`r.v` -> `(*r).v`).
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _thir_fallbacks(source, extra_lib_dirs=None):
    compiler, modules = _compile(source, extra_lib_dirs=extra_lib_dirs)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


_BOX = (
    "from tpy import Int32, Own, nocopy\n"
    "@nocopy\n"
    "class Box:\n"
    "    v: Int32\n"
    "    def __init__(self, v: Int32) -> None:\n"
    "        self.v = v\n"
)


class TestOwnedOptionalMoveOut:
    SRC = _BOX + (
        "class Boxed:\n"
        "    slot: Box | None\n"
        "    def __init__(self, b: Own[Box] | None) -> None:\n"
        "        self.slot = b\n"
        "def move_out(c: bool) -> Own[Box] | None:\n"
        "    tmp: Box | None = None\n"
        "    if c:\n"
        "        tmp = Box(11)\n"
        "    return tmp\n"
        "def main() -> None:\n"
        "    tmp: Box | None = Box(13)\n"
        "    b = Boxed(tmp)\n"
        "    r = move_out(True)\n"
        "    if r is not None:\n"
        "        print(r.v)\n"
        "main()\n"
    )

    def test_stack_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "Boxed(::tpy::ptr_to_optional_move(tmp))" in cpp
        assert "std::optional<Box> r = move_out(true);" in cpp
        assert "(*r).v" in cpp

    def test_new_faces_witnessed(self):
        thir, wit = _lower_ctx_witnessed(self.SRC)
        assert wit.get("move.opt_own_ptr_lift", 0) >= 1
        assert wit.get("call.storage_opt_ret", 0) >= 1
        assert wit.get("field.opt_record_recv", 0) >= 1

    def test_non_last_use_ptr_opt_keeps_rejecting(self):
        # The copy half of the lift (`ptr_to_optional`, non-last-use) is
        # unwitnessed at this slot -- only the move half routes.
        # A COPYABLE record: the later narrowed reads keep `tmp` alive,
        # so the AST renders the copy lift (`ptr_to_optional`) -- the half
        # this row does not carry.
        src = (
            "from tpy import Int32, Own\n"
            "class Box:\n"
            "    v: Int32\n"
            "    def __init__(self, v: Int32) -> None:\n"
            "        self.v = v\n"
            "class Boxed:\n"
            "    slot: Box | None\n"
            "    def __init__(self, b: Own[Box] | None) -> None:\n"
            "        self.slot = b\n"
            "def use() -> Int32:\n"
            "    tmp: Box | None = Box(13)\n"
            "    b = Boxed(tmp)\n"
            "    if tmp is not None:\n"
            "        return tmp.v\n"
            "    return -1\n"
        )
        fell = _thir_fallbacks(src)
        assert any(k.startswith("body:") for k in fell), fell
