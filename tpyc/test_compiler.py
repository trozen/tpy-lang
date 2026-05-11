"""Tests for compiler and build layout."""

from pathlib import Path

import pytest

from . import get_lib_dir, get_runtime_dir
from .compiler import Compiler, BuildLayout
from .diagnostics import SemanticError
from .typesys import (
    INT32, INT64, BIGINT, BOOL, FLOAT, STR, STRVIEW, CHAR, VOID,
    PtrType, make_span, make_list, make_dict, make_array, OptionalType,
    TupleType, UnionType, OwnType, ReadonlyType, NominalType,
)

# Stdlib path needed for from_source when code uses primitive type methods
_STDLIB_DIRS = [get_lib_dir() / "tpy"]


class TestCompilerFromSource:
    def test_simple_program(self):
        source = 'print("hello")'
        compiler = Compiler.from_source(source)
        modules = compiler.compile()
        assert len(modules) == 1
        assert modules[0].name == "main"
        assert modules[0].is_entry_point

    def test_custom_module_name(self):
        source = 'x: int = 1\nprint(x)'
        compiler = Compiler.from_source(source, module_name="test_mod")
        modules = compiler.compile()
        assert modules[0].name == "test_mod"

    def test_matches_file_compilation(self, tmp_path):
        """Verify that from_source and file-based compilation produce the same AST structure."""
        source = 'def add(a: int, b: int) -> int:\n    return a + b\nprint(add(1, 2))'
        src_file = tmp_path / "check.py"
        src_file.write_text(source)

        file_compiler = Compiler(src_file, lib_dirs=_STDLIB_DIRS)
        file_modules = file_compiler.compile()

        source_compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        source_modules = source_compiler.compile()

        assert len(file_modules) == len(source_modules)
        file_ast = file_modules[0].ast
        source_ast = source_modules[0].ast
        assert len(file_ast.functions) == len(source_ast.functions)
        assert len(file_ast.top_level_stmts) == len(source_ast.top_level_stmts)

    @pytest.mark.parametrize(
        ("default_int", "expected"),
        [
            ("Int32", INT32),
            ("Int64", INT64),
            ("BigInt", BIGINT),
        ],
    )
    def test_default_int_setting_controls_unannotated_literals(self, default_int, expected):
        source = "x = 1\n"
        compiler = Compiler.from_source(source, default_int=default_int)
        modules = compiler.compile()
        hpp, _ = compiler.generate_code_to_strings(modules[0])
        expected_cpp = expected.to_cpp()
        assert f"extern {expected_cpp} x;" in hpp

    def test_reassignment_from_bigint_widens_default_int(self):
        source = "x = 0\nx = int(5)\n"
        compiler = Compiler.from_source(source, default_int="Int32", lib_dirs=_STDLIB_DIRS)
        modules = compiler.compile()
        entry = [m for m in modules if m.is_entry_point][0]
        hpp, _ = compiler.generate_code_to_strings(entry)
        assert "extern ::tpy::BigInt x;" in hpp

    def test_invalid_default_int_setting_rejected(self):
        with pytest.raises(ValueError, match="Unsupported default int type"):
            Compiler.from_source("x = 1\n", default_int="Int128")

    def test_native_module_propagation(self):
        """builtins._list inherits native_module from builtins/__init__."""
        lib_dir = get_lib_dir() / "tpy"
        compiler = Compiler.from_source("x = [1]\n", lib_dirs=[lib_dir])
        compiler.compile()
        mod = compiler.modules.get("builtins._list")
        if mod is not None:
            assert mod.ast.directives.native_module
            _, cpp = compiler.generate_code_to_strings(mod)
            assert cpp == ""


class TestCodegenRegression:
    def test_generic_ctor_invalid_arg_rejected(self, tmp_path):
        """Invalid generic constructor arg (Int32 for Span[T] param) must be rejected by sema."""
        source = (
            'from tpy import Span, Int32\n'
            'x: Span[Int32] = Span[Int32](Int32(1))\n'
        )
        src_file = tmp_path / "test.py"
        src_file.write_text(source)
        compiler = Compiler(src_file, lib_dirs=[get_lib_dir() / "tpy"])
        with pytest.raises(SemanticError, match="cannot be constructed from"):
            compiler.compile()


class TestSendSync:
    """Test Send/Sync auto-derivation on built-in types."""

    # -- Primitive value types: all Send + Sync --

    @pytest.mark.parametrize("typ", [INT32, INT64, BIGINT, BOOL, FLOAT, STR, CHAR])
    def test_primitives_are_send_and_sync(self, typ):
        assert typ.is_send()
        assert typ.is_sync()

    # -- StrView: borrows, not Send, but Sync (read-only) --

    def test_strview_not_send(self):
        assert not STRVIEW.is_send()

    def test_strview_is_sync(self):
        assert STRVIEW.is_sync()

    # -- Ptr: not Send; Sync only if readonly --

    def test_ptr_not_send(self):
        assert not PtrType(INT32).is_send()
        assert not PtrType(INT32, is_readonly=True).is_send()

    def test_mutable_ptr_not_sync(self):
        assert not PtrType(INT32).is_sync()

    def test_readonly_ptr_sync_if_pointee_sync(self):
        assert PtrType(INT32, is_readonly=True).is_sync()
        # Ptr[readonly[list[T]]]: list is not Sync
        assert not PtrType(make_list(INT32), is_readonly=True).is_sync()

    # -- Span: not Send; Sync only if readonly --

    def test_span_not_send(self):
        assert not make_span(INT32).is_send()
        assert not make_span(INT32, is_readonly=True).is_send()

    def test_mutable_span_not_sync(self):
        assert not make_span(INT32).is_sync()

    def test_readonly_span_sync_if_element_sync(self):
        assert make_span(INT32, is_readonly=True).is_sync()

    # -- list: Send if element Send, never Sync --

    def test_list_send_if_element_send(self):
        assert make_list(INT32).is_send()
        assert not make_list(PtrType(INT32)).is_send()

    def test_list_not_sync(self):
        assert not make_list(INT32).is_sync()

    # -- dict: Send if elements Send, never Sync --

    def test_dict_send_if_elements_send(self):
        assert make_dict(STR, INT32).is_send()
        assert not make_dict(STR, PtrType(INT32)).is_send()

    def test_dict_not_sync(self):
        assert not make_dict(STR, INT32).is_sync()

    # -- Array: Send/Sync based on element --

    def test_array_send_sync_based_on_element(self):
        assert make_array(INT32, 10).is_send()
        assert make_array(INT32, 10).is_sync()
        assert not make_array(PtrType(INT32), 10).is_send()

    # -- Tuple: Send/Sync if all elements are --

    def test_tuple_send_sync(self):
        assert TupleType((INT32, STR)).is_send()
        assert TupleType((INT32, STR)).is_sync()
        assert not TupleType((INT32, PtrType(STR))).is_send()

    # -- Optional: delegates to inner --

    def test_optional_delegates(self):
        assert OptionalType(INT32).is_send()
        # OptionalType inner = NominalType (non-Send record) -- delegates through.
        # We can't use OptionalType(PtrType(...)) here because the __new__ collapse
        # would return a bare PtrType (Ptr[T] is already nullable), so the test
        # would exercise PtrType, not OptionalType's delegation.
        record = NominalType("R", (), _module_qname="__main__.R")
        assert not OptionalType(record).is_send()

    # -- Union: all members --

    def test_union_all_members(self):
        assert UnionType((INT32, STR)).is_send()
        assert not UnionType((INT32, PtrType(STR))).is_send()

    # -- Own: delegates to wrapped --

    def test_own_delegates(self):
        assert OwnType(make_list(INT32)).is_send()
        assert not OwnType(make_list(INT32)).is_sync()

    # -- readonly: makes mutable containers Sync --

    def test_readonly_makes_sync(self):
        assert not make_list(INT32).is_sync()
        assert ReadonlyType(make_list(INT32)).is_sync()

    def test_readonly_of_readonly_ptr_is_sync(self):
        rop = PtrType(INT32, is_readonly=True)
        assert not rop.is_send()
        assert rop.is_sync()
        assert ReadonlyType(rop).is_sync()

    def test_readonly_of_non_send_not_sync(self):
        # readonly[list[Ptr[T]]]: Ptr not Send, so list not Send, not Sync
        assert not ReadonlyType(make_list(PtrType(INT32))).is_sync()


class TestSendSyncRecordDerivation:
    """Test Send/Sync auto-derivation on user-defined records."""

    def test_record_with_value_fields_is_send_sync(self):
        source = (
            "from tpy import Int32\n"
            "class Point:\n"
            "    x: Int32\n"
            "    y: Int32\n"
            "    def __init__(self, x: Int32, y: Int32) -> None:\n"
            "        self.x = x\n"
            "        self.y = y\n"
            "def main() -> None:\n"
            "    p = Point(Int32(1), Int32(2))\n"
            "    print(p.x)\n"
            "main()\n"
        )
        compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
        compiler.compile()
        point_type = NominalType("Point", (), _module_qname="__main__.Point")
        assert point_type.is_send()
        assert point_type.is_sync()

    def test_record_with_ptr_field_not_send(self, tmp_path):
        source = (
            "from tpy import Ptr, Int32, take_ptr\n"
            "class Wrapper:\n"
            "    x: Int32\n"
            "    def __init__(self, x: Int32) -> None:\n"
            "        self.x = x\n"
            "class Holder:\n"
            "    p: Ptr[Wrapper]\n"
            "    def __init__(self, p: Ptr[Wrapper]) -> None:\n"
            "        self.p = p\n"
            "def main() -> None:\n"
            "    w = Wrapper(Int32(1))\n"
            "    h = Holder(take_ptr(w))\n"
            "    print(h.p.x)\n"
            "main()\n"
        )
        src_file = tmp_path / "test.py"
        src_file.write_text(source)
        compiler = Compiler(src_file, lib_dirs=[get_lib_dir() / "tpy"])
        compiler.compile()
        holder_type = NominalType("Holder", (), _module_qname="__main__.Holder")
        assert not holder_type.is_send()
        assert not holder_type.is_sync()

    def test_record_with_list_field_send_not_sync(self):
        source = (
            "from tpy import Int32\n"
            "class Container:\n"
            "    items: list[Int32]\n"
            "    def __init__(self) -> None:\n"
            "        self.items = [Int32(1)]\n"
            "def main() -> None:\n"
            "    c = Container()\n"
            "    print(len(c.items))\n"
            "main()\n"
        )
        from . import get_lib_dir
        lib_dirs = [get_lib_dir() / "tpy"]
        compiler = Compiler.from_source(source, lib_dirs=lib_dirs)
        compiler.compile()
        container_type = NominalType("Container", (), _module_qname="__main__.Container")
        assert container_type.is_send()
        assert not container_type.is_sync()


class TestBuildLayout:
    def setup_method(self):
        self.layout = BuildLayout(Path("/out"), "main")

    def test_simple_module_hpp(self):
        assert self.layout.hpp_path("main") == Path("/out/main.d/include/main.hpp")

    def test_simple_module_cpp(self):
        assert self.layout.cpp_path("main") == Path("/out/main.d/src/main.cpp")

    def test_dotted_module_hpp(self):
        assert self.layout.hpp_path("pkg.mod") == Path("/out/main.d/include/pkg/mod.hpp")

    def test_dotted_module_cpp(self):
        assert self.layout.cpp_path("pkg.mod") == Path("/out/main.d/src/pkg/mod.cpp")

    def test_deeply_nested_hpp(self):
        assert self.layout.hpp_path("a.b.c.d") == Path("/out/main.d/include/a/b/c/d.hpp")

    def test_deeply_nested_cpp(self):
        assert self.layout.cpp_path("a.b.c.d") == Path("/out/main.d/src/a/b/c/d.cpp")

    def test_binary_path(self):
        assert self.layout.binary_path() == Path("/out/main.d/main")

    def test_binary_path_with_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="debug")
        assert layout.binary_path() == Path("/out/main.d/debug/main")

    def test_binary_path_release_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="release")
        assert layout.binary_path() == Path("/out/main.d/release/main")

    def test_build_dir_no_variant(self):
        assert self.layout.build_dir == Path("/out/main.d")

    def test_build_dir_with_variant(self):
        layout = BuildLayout(Path("/out"), "main", build_variant="debug")
        assert layout.build_dir == Path("/out/main.d/debug")


class TestGenerateCmake:
    """Tests for sources.cmake generation and runtime bundling."""

    def _make_layout(self, tmp_path: Path) -> BuildLayout:
        layout = BuildLayout(tmp_path, "app")
        layout.include_dir.mkdir(parents=True)
        layout.src_dir.mkdir(parents=True)
        return layout

    def _dummy_cpp(self, layout: BuildLayout) -> list[Path]:
        cpp = layout.src_dir / "app.cpp"
        cpp.write_text("// generated")
        return [cpp]

    def test_bundle_runtime_copies_headers(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=True,
        )
        bundled = layout.root_dir / "runtime" / "include" / "tpy"
        assert bundled.is_dir()
        assert (bundled / "tpy.hpp").is_file()

    def test_bundle_runtime_cmake_uses_local_path(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=True,
        )
        content = cmake_path.read_text()
        assert "${CMAKE_CURRENT_LIST_DIR}/runtime/include" in content
        # No absolute or parent-traversal paths to the runtime
        assert "../../" not in content.split("TPYC_INCLUDE_DIRS")[1].split(")")[0]

    def test_no_bundle_runtime_uses_relative_path(self, tmp_path: Path):
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=self._dummy_cpp(layout),
            bundle_runtime=False,
        )
        content = cmake_path.read_text()
        bundled = layout.root_dir / "runtime" / "include"
        assert not bundled.exists()
        assert "runtime/include" not in content
        # Should reference the runtime via a relative or absolute path
        lines = content.splitlines()
        inc_lines = []
        in_inc = False
        for line in lines:
            if line.startswith("set(TPYC_INCLUDE_DIRS"):
                in_inc = True
                continue
            if in_inc:
                if line.strip() == ")":
                    break
                inc_lines.append(line.strip())
        assert len(inc_lines) == 2
        assert "../" in inc_lines[1] or str(runtime_inc) in inc_lines[1]

    def test_bundle_runtime_idempotent(self, tmp_path: Path):
        """Repeated calls with bundle_runtime=True succeed."""
        layout = self._make_layout(tmp_path)
        runtime_inc = get_runtime_dir() / "cpp" / "include"
        cpp_files = self._dummy_cpp(layout)
        layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=cpp_files,
            bundle_runtime=True,
        )
        cmake_path = layout.generate_cmake(
            runtime_include_dir=runtime_inc,
            cpp_files=cpp_files,
            bundle_runtime=True,
        )
        content = cmake_path.read_text()
        assert "${CMAKE_CURRENT_LIST_DIR}/runtime/include" in content
        assert (layout.root_dir / "runtime" / "include" / "tpy" / "tpy.hpp").is_file()


def _make_skeleton_module(records=(), protocols=(), functions=(), enums=()):
    """Build a minimal CompiledModule with given exports populated
    and a matching `_skeleton_ids` snapshot, as if pre-populate had
    just run on it. No sema needed -- the verifier only consults the
    snapshot and the exports.
    """
    from .compiler import CompiledModule, ModuleExports, _SkeletonSnapshot
    from .parse import TpyModule
    from .parse.nodes import ModuleDirectives

    ast = TpyModule(
        records=[], functions=[], protocols=[], enums=[],
        top_level_stmts=[], source_lines=[], imports={},
        tpy_star_import=False, star_imports=set(), user_module_imports={},
        module_aliases={}, bare_module_imports=set(), type_aliases={},
        directives=ModuleDirectives(),
    )
    exports = ModuleExports()
    for r in records:
        exports.records[r.name] = r
    for p in protocols:
        exports.protocols[p.name] = p
    for name, fi_list in functions:
        exports.functions[name] = list(fi_list)
    for name, nominal in enums:
        exports.enums[name] = nominal

    snap = _SkeletonSnapshot(
        records={n: id(o) for n, o in exports.records.items()},
        protocols={n: id(o) for n, o in exports.protocols.items()},
        functions={n: (id(lst), id(lst[0])) if lst else (0, 0)
                   for n, lst in exports.functions.items()},
    )
    return CompiledModule(
        name="m", path=Path("<test>"), ast=ast, exports=exports,
        is_entry_point=True, _skeleton_ids=snap,
    )


class TestSkeletonAdoptionAssertion:
    """Pins the `_verify_skeleton_adoption` invariant: every
    pre-populated skeleton object/list must be adopted in place by
    its registration entrypoint, so peer registries that captured a
    reference at bind_imports time see freshly-finalized data.

    Five of these tests use `_make_skeleton_module` to hand-build a
    minimal CompiledModule + matching snapshot and exercise the
    verifier directly, with no sema involved. The first test does a
    full real compile to confirm the production path passes the
    assertion in normal use (no false positives).
    """

    def test_correctly_adopted_real_compile(self):
        """A normal compile passes the assertion (smoke-test against
        false positives in the production pipeline)."""
        source = (
            "class A:\n"
            "    val: int\n"
            "    def __init__(self, v: int) -> None:\n"
            "        self.val = v\n"
            "def helper() -> int:\n"
            "    return 42\n"
        )
        Compiler.from_source(source).compile()  # raises if violated

    def test_record_identity_mismatch_fires(self):
        from .typesys import RecordInfo
        rec = RecordInfo(name="A", fields=[], module="m", defining_module="m")
        compiled = _make_skeleton_module(records=[rec])
        compiled.exports.records["A"] = RecordInfo(
            name="A", fields=[], module="m", defining_module="m",
        )
        with pytest.raises(AssertionError, match=r"record 'A'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_protocol_identity_mismatch_fires(self):
        from .typesys import ProtocolInfo
        proto = ProtocolInfo(name="P", methods=[], type_params=[],
                             module="m", is_dynamic=False)
        compiled = _make_skeleton_module(protocols=[proto])
        compiled.exports.protocols["P"] = ProtocolInfo(
            name="P", methods=[], type_params=[],
            module="m", is_dynamic=False,
        )
        with pytest.raises(AssertionError, match=r"protocol 'P'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_function_with_neither_list_nor_fi_identity_fires(self):
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="f", params=[], return_type=VOID,
                                qualified_name="m.f", originating_module="m")
        compiled = _make_skeleton_module(functions=[("f", [original])])
        compiled.exports.functions["f"] = [
            FunctionInfo(name="f", params=[], return_type=VOID,
                         qualified_name="m.f", originating_module="m"),
        ]
        with pytest.raises(AssertionError, match=r"function 'f'"):
            Compiler._verify_skeleton_adoption(compiled)

    def test_function_inner_fi_preserved_passes(self):
        # register_function adoption shape: fresh list, skeleton FI inside.
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="f", params=[], return_type=VOID,
                                qualified_name="m.f", originating_module="m")
        compiled = _make_skeleton_module(functions=[("f", [original])])
        compiled.exports.functions["f"] = [original]
        Compiler._verify_skeleton_adoption(compiled)

    def test_function_list_mutated_in_place_passes(self):
        # register_overload_group adoption shape: skeleton list cleared
        # and re-extended with fresh FIs; list identity preserved even
        # though the inner FI identity is gone.
        from .typesys import FunctionInfo, VOID
        original = FunctionInfo(name="g", params=[], return_type=VOID,
                                qualified_name="m.g", originating_module="m")
        compiled = _make_skeleton_module(functions=[("g", [original])])
        skeleton_list = compiled.exports.functions["g"]
        skeleton_list.clear()
        skeleton_list.extend([
            FunctionInfo(name="g", params=[], return_type=VOID,
                         qualified_name="m.g", originating_module="m"),
            FunctionInfo(name="g", params=[], return_type=VOID,
                         qualified_name="m.g", originating_module="m"),
        ])
        Compiler._verify_skeleton_adoption(compiled)

    def test_enum_not_checked(self):
        from .typesys import NominalType
        compiled = _make_skeleton_module(
            enums=[("Color", NominalType(name="Color", type_args=(),
                                         _module_qname="m.Color"))],
        )
        compiled.exports.enums["Color"] = NominalType(
            name="Color", type_args=(), _module_qname="m.Color",
        )
        Compiler._verify_skeleton_adoption(compiled)
