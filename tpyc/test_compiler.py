"""Tests for compiler and build layout."""

from pathlib import Path

import pytest

from .compiler import Compiler, BuildLayout
from .sema.diagnostics import SemanticError
from .typesys import INT32, INT64, BIGINT


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
        assert "extern tpy::BigInt x;" in hpp

    def test_invalid_default_int_setting_rejected(self):
        with pytest.raises(ValueError, match="Unsupported default int type"):
            Compiler.from_source("x = 1\n", default_int="Int128")


class TestCodegenRegression:
    def test_generic_ctor_invalid_arg_rejected(self):
        """Invalid generic constructor arg (Int32 for Span[T] param) must be rejected by sema."""
        source = (
            'from tpy import Span, Int32\n'
            'x: Span[Int32] = Span[Int32](Int32(1))\n'
        )
        compiler = Compiler.from_source(source)
        with pytest.raises(SemanticError, match="cannot be constructed from"):
            compiler.compile()


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
