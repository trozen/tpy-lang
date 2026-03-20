"""Tests for compiler and build layout."""

from pathlib import Path

import pytest

from . import get_lib_dir
from .compiler import Compiler, BuildLayout
from .sema.diagnostics import SemanticError
from .typesys import (
    INT32, INT64, BIGINT, BOOL, FLOAT, STR, CHAR, VOID,
    PtrType, SpanType, ListType, DictType, ArrayType, OptionalType,
    TupleType, UnionType, OwnType, ReadonlyType, StrViewType, NamedType,
)


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

        file_compiler = Compiler(src_file)
        file_modules = file_compiler.compile()

        source_compiler = Compiler.from_source(source)
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
        compiler = Compiler.from_source(source, default_int="Int32")
        modules = compiler.compile()
        hpp, _ = compiler.generate_code_to_strings(modules[0])
        assert "extern ::tpy::BigInt x;" in hpp

    def test_invalid_default_int_setting_rejected(self):
        with pytest.raises(ValueError, match="Unsupported default int type"):
            Compiler.from_source("x = 1\n", default_int="Int128")


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
        assert not StrViewType().is_send()

    def test_strview_is_sync(self):
        assert StrViewType().is_sync()

    # -- Ptr: not Send; Sync only if readonly --

    def test_ptr_not_send(self):
        assert not PtrType(INT32).is_send()
        assert not PtrType(INT32, is_readonly=True).is_send()

    def test_mutable_ptr_not_sync(self):
        assert not PtrType(INT32).is_sync()

    def test_readonly_ptr_sync_if_pointee_sync(self):
        assert PtrType(INT32, is_readonly=True).is_sync()
        # Ptr[readonly[list[T]]]: list is not Sync
        assert not PtrType(ListType(INT32), is_readonly=True).is_sync()

    # -- Span: not Send; Sync only if readonly --

    def test_span_not_send(self):
        assert not SpanType(INT32).is_send()
        assert not SpanType(INT32, is_readonly=True).is_send()

    def test_mutable_span_not_sync(self):
        assert not SpanType(INT32).is_sync()

    def test_readonly_span_sync_if_element_sync(self):
        assert SpanType(INT32, is_readonly=True).is_sync()

    # -- list: Send if element Send, never Sync --

    def test_list_send_if_element_send(self):
        assert ListType(INT32).is_send()
        assert not ListType(PtrType(INT32)).is_send()

    def test_list_not_sync(self):
        assert not ListType(INT32).is_sync()

    # -- dict: Send if elements Send, never Sync --

    def test_dict_send_if_elements_send(self):
        assert DictType(STR, INT32).is_send()
        assert not DictType(STR, PtrType(INT32)).is_send()

    def test_dict_not_sync(self):
        assert not DictType(STR, INT32).is_sync()

    # -- Array: Send/Sync based on element --

    def test_array_send_sync_based_on_element(self):
        assert ArrayType(INT32, 10).is_send()
        assert ArrayType(INT32, 10).is_sync()
        assert not ArrayType(PtrType(INT32), 10).is_send()

    # -- Tuple: Send/Sync if all elements are --

    def test_tuple_send_sync(self):
        assert TupleType((INT32, STR)).is_send()
        assert TupleType((INT32, STR)).is_sync()
        assert not TupleType((INT32, PtrType(STR))).is_send()

    # -- Optional: delegates to inner --

    def test_optional_delegates(self):
        assert OptionalType(INT32).is_send()
        assert not OptionalType(PtrType(INT32)).is_send()

    # -- Union: all members --

    def test_union_all_members(self):
        assert UnionType((INT32, STR)).is_send()
        assert not UnionType((INT32, PtrType(STR))).is_send()

    # -- Own: delegates to wrapped --

    def test_own_delegates(self):
        assert OwnType(ListType(INT32)).is_send()
        assert not OwnType(ListType(INT32)).is_sync()

    # -- readonly: makes mutable containers Sync --

    def test_readonly_makes_sync(self):
        assert not ListType(INT32).is_sync()
        assert ReadonlyType(ListType(INT32)).is_sync()

    def test_readonly_of_readonly_ptr_is_sync(self):
        rop = PtrType(INT32, is_readonly=True)
        assert not rop.is_send()
        assert rop.is_sync()
        assert ReadonlyType(rop).is_sync()

    def test_readonly_of_non_send_not_sync(self):
        # readonly[list[Ptr[T]]]: Ptr not Send, so list not Send, not Sync
        assert not ReadonlyType(ListType(PtrType(INT32))).is_sync()


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
        compiler = Compiler.from_source(source)
        compiler.compile()
        point_type = NamedType("Point")
        assert point_type.is_send()
        assert point_type.is_sync()

    def test_record_with_ptr_field_not_send(self):
        source = (
            "from tpy import Ptr, Int32\n"
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
            "    h = Holder(Ptr(w))\n"
            "    print(h.p.x)\n"
            "main()\n"
        )
        compiler = Compiler.from_source(source)
        compiler.compile()
        holder_type = NamedType("Holder")
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
        container_type = NamedType("Container")
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
