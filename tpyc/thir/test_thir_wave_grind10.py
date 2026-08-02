"""Wave 10 of the grind loop: the top_level:expr.call site -- three rows.

The zero-arg container instantiation with EXPLICIT type args
(`items = list[Int32]()`) rides the existing `call.instantiation_empty`
arm: sema folds the spelling into `call_type` and the render reads
nothing else, so the `not e.type_args` exclusion was a phantom fence
(the user-generic subscript form is excluded by `subscript_callee`,
which builtins never set). The zero-arg builtin-container ctor-path
sibling (`Array[Int32, 8]()`, a constructor fi so it never reaches the
instantiation arm) gets its own `_ctor_instantiation_ok` row. And the
global ptr-slot's address-of catch-all widens from method calls to free
calls (`p = &(get_item<Point>((*points), 0));`), backed by the
RECEIVER-use `call.recv_borrow_ret` row in `_call_use_supported`.
"""

from ..codegen_cpp.context import CodeGenOptions
from .testutil import (
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)


def _thir_fallbacks(source):
    compiler, modules = _compile(source)
    entry = _entry(modules)
    compiler.generate_code_to_strings(
        entry, options=CodeGenOptions(emit_source_comments=False,
                                      comment_line_numbers=False,
                                      thir_codegen=True))
    return dict(compiler._thir_fallback)


class TestZeroArgTypedInstantiation:
    SRC = (
        "from tpy import Int32\n"
        "def main() -> None:\n"
        "    items = list[Int32]()\n"
        "    items.append(100)\n"
        "    d = dict[str, Int32]()\n"
        "    print(len(items), len(d))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::vector<int32_t>()" in cpp

    def test_faces(self):
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("call.instantiation_empty", 0) >= 2


class TestZeroArgUserGenericTakesOwnArm:
    # The subscript-spelled user generic (`Stack[Int32]()`) sets
    # `subscript_callee` and rides its own instantiation arm -- the widened
    # empty-instantiation guard must not capture it (the boundary is
    # NON-capture, not rejection: the shape already routed on that arm).
    SRC = (
        "from tpy import Int32\n"
        "class Stack[T]:\n"
        "    items: list[T]\n"
        "    def __init__(self) -> None:\n"
        "        self.items = []\n"
        "def main() -> None:\n"
        "    s = Stack[Int32]()\n"
        "    print(len(s.items))\n"
        "main()\n"
    )

    def test_routes_off_its_own_arm(self):
        _assert_routes_byte_identical(self.SRC)
        _thir, w = _lower_ctx_witnessed(self.SRC)
        assert w.get("call.instantiation_empty", 0) == 0
        assert w.get("ctor.container_empty_instantiation", 0) == 0


class TestZeroArgArrayInstantiation:
    # `Array[Int32, 8]()` at a LOCAL decl rides the empty-instantiation arm
    # (the Array family admission); the same call at a GLOBAL slot arrives
    # with a constructor fi and takes the `_ctor_instantiation_ok` container
    # row instead -- both render the bare `type_cpp()`.
    SRC = (
        "from tpy import Int32, Array\n"
        "def main() -> None:\n"
        "    data = Array[Int32, 8]()\n"
        "    data[0] = 5\n"
        "    print(data[0])\n"
        "main()\n"
    )

    GLOBAL_SRC = (
        "from tpy import Int32, Array\n"
        "data = Array[Int32, 8]()\n"
        "data[0] = 5\n"
        "print(data[0])\n"
    )

    def test_local_routes_byte_identical(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "std::array<int32_t, 8>()" in cpp

    def test_global_slot_routes_byte_identical(self):
        compiler, modules = _compile(self.GLOBAL_SRC)
        entry = _entry(modules)
        _hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=True))
        assert not dict(compiler._thir_fallback)
        assert ("static std::array<int32_t, 8> __global_slot_1 "
                "= std::array<int32_t, 8>();") in cpp
        # TPy source resolves this call without a constructor fi, so it
        # rides the instantiation-empty arm here. The ctor-path row
        # (`ctor.container_empty_instantiation`) is witnessed by the pascal
        # corpus pair (bubble_sort / panic_array_index, unmarked so the
        # ratchet enforces their routing) -- the frontend synthesizes the
        # same call WITH a constructor fi.
        assert compiler._thir_face_witnesses.get(
            "call.instantiation_empty", 0) >= 1


_POINTS = (
    "from tpy import Int32\n"
    "class Point:\n"
    "    x: Int32\n"
    "    y: Int32\n"
    "    def __init__(self, x: Int32, y: Int32) -> None:\n"
    "        self.x = x\n"
    "        self.y = y\n"
    "def get_item[T](items: list[T], idx: Int32) -> T:\n"
    "    return items[idx]\n"
)


class TestGlobalAddrFreeCall:
    # The global ptr-slot's address-of catch-all over a BORROW-returning
    # FREE call: `p = &(get_item<Point>((*points), 0));`.
    SRC = (
        _POINTS +
        "points = [Point(1, 2), Point(3, 4)]\n"
        "p = get_item(points, 0)\n"
        "print(p.x)\n"
    )

    def test_routes_byte_identical_and_witnesses(self):
        hpp, cpp = _assert_routes_byte_identical(self.SRC)
        assert "p = &(get_item<Point>((*points), 0));" in cpp
        # Top-level statements lower in the module-init walk, not
        # lower_module -- read the witnesses off the full pipeline run.
        compiler, modules = _compile(self.SRC)
        entry = _entry(modules)
        compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=True))
        w = compiler._thir_face_witnesses
        assert w.get("top_level.global_addr_call", 0) >= 1
        assert w.get("call.recv_borrow_ret", 0) >= 1


class TestGlobalAddrSubclassStillRejects:
    # "Same-type only": a subclass borrow return would retype the slot (the
    # polymorphic arm's business) -- the widened row must keep rejecting it.
    SRC = (
        "from tpy import Int32\n"
        "class Animal:\n"
        "    kind: Int32\n"
        "    def __init__(self, kind: Int32) -> None:\n"
        "        self.kind = kind\n"
        "class Dog(Animal):\n"
        "    def __init__(self) -> None:\n"
        "        super().__init__(1)\n"
        "def get_item[T](items: list[T], idx: Int32) -> T:\n"
        "    return items[idx]\n"
        "dogs = [Dog()]\n"
        "a: Animal = get_item(dogs, 0)\n"
        "print(a.kind)\n"
    )

    def test_subclass_borrow_falls_back(self):
        fell = _thir_fallbacks(self.SRC)
        assert any(k.startswith("top_level:") for k in fell), fell


class TestPascalCtorPathContainerWitness:
    # The `_ctor_instantiation_ok` zero-arg container row fires only when
    # the call arrives WITH a constructor fi -- plain TPy source resolves
    # `Array[Int32, 8]()` without one, so the witness needs the pascal
    # frontend, whose default-array init synthesizes exactly that node.
    PAS = (
        "program tiny;\n"
        "var\n"
        "  data: array[1..4] of integer;\n"
        "begin\n"
        "  data[1] := 7;\n"
        "  writeln(data[1]);\n"
        "end.\n"
    )

    def test_pascal_default_array_witnesses_the_ctor_row(self, tmp_path):
        from pathlib import Path
        from ..frontend_plugin import FrontendRegistry, load_plugin
        from ..compiler import Compiler
        plugin = load_plugin("frontends/pascal/pascal_frontend.py",
                             {"sdl": "off"})
        reg = FrontendRegistry()
        reg.register(plugin)
        src = tmp_path / "main.pas"
        src.write_text(self.PAS)
        lib_dirs = ([Path(p) for p in plugin.library_paths()]
                    + [Path("lib/tpy")])
        compiler = Compiler(src, default_int="Int32", lib_dirs=lib_dirs,
                            frontend_registry=reg)
        modules = compiler.compile()
        entry = [m for m in modules if m.is_entry_point][0]
        _hpp, cpp = compiler.generate_code_to_strings(
            entry, options=CodeGenOptions(emit_source_comments=False,
                                          comment_line_numbers=False,
                                          thir_codegen=True))
        assert not dict(compiler._thir_fallback)
        assert compiler._thir_face_witnesses.get(
            "ctor.container_empty_instantiation", 0) >= 1
        assert "= std::array<int32_t, 4>();" in cpp
