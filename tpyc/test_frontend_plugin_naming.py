"""A frontend plugin can own module naming + resolution.

Exercises the optional FrontendPlugin.module_name / resolve_module hooks: a
plugin names a flat source file by its own policy (NOT the file's path) and
resolves that dotted name back to the file, so a module is registered -- and
importable -- under the plugin-chosen name regardless of disk layout. A plugin
that doesn't implement the hooks (or a `.py` file) is unaffected, covered by
the existing test_compiler suite.
"""

from pathlib import Path

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import Diagnostic, DiagnosticLevel
from .frontend_plugin import (
    FrontendOutput, FrontendPlugin, FrontendRegistry, WorkspaceContext)
from .frontend_ir import BoolLit, FrontendModule, Function, NamedType, Return

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


class _NamingPlugin(FrontendPlugin):
    """Claims `.fk`. Names any `<dir>/<stem>.fk` as the dotted `plug.<stem>`
    (path-independent) and resolves `plug.<stem>` back to the flat file on the
    search path. parse() synthesises a module exporting `answer() -> True`
    (builtin `bool`, so the synthesised module needs no `from tpy import`)."""

    name = "fk"
    extensions = (".fk",)

    def parse(self, ctx: WorkspaceContext,
              module_name: str, file_path: Path) -> FrontendOutput:
        answer = Function(
            name="answer",
            return_type=NamedType(name="bool"),
            body=(Return(value=BoolLit(value=True)),),
        )
        return FrontendOutput(module=FrontendModule(
            qname=module_name, source_language="fk", functions=(answer,)))

    def module_name(self, search_dirs: tuple[Path, ...],
                    path: Path) -> str | None:
        return f"plug.{path.stem}"

    def resolve_module(self, search_dirs: tuple[Path, ...],
                       dotted_name: str) -> Path | None:
        if not dotted_name.startswith("plug."):
            return None
        stem = dotted_name.split(".")[-1]
        for d in search_dirs:
            cand = d / f"{stem}.fk"
            if cand.is_file():
                return cand
        return None


def _registry() -> FrontendRegistry:
    reg = FrontendRegistry()
    reg.register(_NamingPlugin({}))
    return reg


def _errors(compiler: Compiler) -> list[Diagnostic]:
    return [d for d in compiler.diagnostics if d.level == DiagnosticLevel.ERROR]


def test_py_imports_plugin_module_by_plugin_chosen_name(tmp_path):
    # A flat `thing.fk` is registered under the plugin's `plug.thing`, NOT its
    # path stem, and a .py resolves the import to it.
    (tmp_path / "thing.fk").write_text("")  # content unused; plugin synthesises IR
    (tmp_path / "main.py").write_text(
        "from plug.thing import answer\nprint(answer())\n")
    compiler = Compiler(tmp_path / "main.py", lib_dirs=_STDLIB_DIRS,
                        frontend_registry=_registry())
    modules = compiler.compile()
    assert not _errors(compiler), _errors(compiler)
    names = {m.name for m in modules}
    assert "plug.thing" in names
    assert "thing" not in names  # the path-stem name is NOT used


def test_plugin_file_as_entry_is_named_by_plugin(tmp_path):
    # The entry naming honours the plugin too (not just imported modules).
    (tmp_path / "thing.fk").write_text("")
    compiler = Compiler(tmp_path / "thing.fk", lib_dirs=_STDLIB_DIRS,
                        frontend_registry=_registry())
    modules = compiler.compile()
    assert not _errors(compiler), _errors(compiler)
    assert any(m.name == "plug.thing" and m.is_entry_point for m in modules)
