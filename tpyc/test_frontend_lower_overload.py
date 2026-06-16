"""A frontend plugin can emit a `typing.overload` group through the IR: several
same-named free `Function`s flagged `is_overload`, each self-contained (a body
or `@native`), lower to `is_overload_stub` TpyFunctions that sema groups and
resolves by argument type at the call site -- the same path source-level
`@overload` functions take. Without `is_overload` the IR cannot express
overloads (the `typing.overload` builtin decorator has no IR lowering).
"""

from pathlib import Path

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import Diagnostic, DiagnosticLevel
from .frontend_ir.lower import lower_module
from .frontend_ir.nodes import (
    BoolLit, Decorator, FrontendModule, Function, IntLit, NamedType, Param,
    Return, StrLit)
from .frontend_plugin import (
    FrontendOutput, FrontendPlugin, FrontendRegistry, WorkspaceContext)

_STDLIB_DIRS = [get_lib_dir() / "tpy"]


# --- lowering unit (no compile) -------------------------------------------

def _overload_fn(param_type: str, ret: str) -> Function:
    return Function(
        name="pick", is_overload=True,
        params=(Param(name="x", type=NamedType(name=param_type)),),
        return_type=NamedType(name=ret),
        body=(Return(value=IntLit(value=0)),))


def test_is_overload_lowers_to_overload_stub():
    # Two same-named is_overload Functions -> two is_overload_stub TpyFunctions.
    # Bodied members keep is_stub=False (the body is the impl) and satisfy the
    # has_implementation gate sema requires for an impl-less overload group.
    res = lower_module(
        FrontendModule(qname="m", functions=(
            _overload_fn("Int32", "Int32"),
            _overload_fn("float", "Int64"))),
        "testplugin")
    assert not res.diagnostics, res.diagnostics
    fns = res.module.functions
    assert [f.name for f in fns] == ["pick", "pick"]
    for f in fns:
        assert f.is_overload_stub
        assert not f.is_stub
        assert f.has_implementation


def test_native_is_overload_member_keeps_both_stub_flags():
    # A @native overload member is self-contained via its C++ binding, not a
    # body: it must carry is_stub (declaration-only codegen) AND
    # is_overload_stub (group membership) at once -- the native lowering branch
    # must not clobber the overload flag.
    res = lower_module(
        FrontendModule(qname="m", functions=(
            Function(
                name="pick", is_overload=True,
                params=(Param(name="x", type=NamedType(name="Int32")),),
                return_type=NamedType(name="Int32"),
                decorators=(Decorator(
                    name="tpy.native", args=(StrLit(value="pick_i32"),)),)),)),
        "testplugin")
    assert not res.diagnostics, res.diagnostics
    fn = res.module.functions[0]
    assert fn.is_overload_stub
    assert fn.is_stub
    assert fn.native_name == "pick_i32"


def test_without_is_overload_no_stub():
    # The flag is opt-in: a plain Function lowers to a non-overload TpyFunction.
    res = lower_module(
        FrontendModule(qname="m", functions=(
            Function(name="f", return_type=NamedType(name="Int32"),
                     body=(Return(value=IntLit(value=0)),)),)),
        "testplugin")
    assert not res.diagnostics, res.diagnostics
    assert not res.module.functions[0].is_overload_stub


# --- end-to-end (plugin emits IR, full sema + codegen) --------------------

class _OverloadPlugin(FrontendPlugin):
    """Claims `.ovl`; synthesises `plug.<stem>` exporting an overload group
    `pick`: `pick(bool) -> bool` and `pick(str) -> bool`. Distinct, unrelated
    param types so that a *wrong* dispatch (bool<->str) would be a type error --
    compile success proves resolution is argument-type-directed."""

    name = "ovl"
    extensions = (".ovl",)

    def parse(self, ctx: WorkspaceContext,
              module_name: str, file_path: Path) -> FrontendOutput:
        def pick(pt: str) -> Function:
            return Function(
                name="pick", is_overload=True,
                params=(Param(name="x", type=NamedType(name=pt)),),
                return_type=NamedType(name="bool"),
                body=(Return(value=BoolLit(value=True)),))
        return FrontendOutput(module=FrontendModule(
            qname=module_name, source_language="ovl",
            functions=(pick("bool"), pick("str"))))

    def module_name(self, search_dirs, path):
        return f"plug.{path.stem}"

    def resolve_module(self, search_dirs, dotted_name):
        if not dotted_name.startswith("plug."):
            return None
        stem = dotted_name.split(".")[-1]
        for d in search_dirs:
            cand = d / f"{stem}.ovl"
            if cand.is_file():
                return cand
        return None


def _errors(compiler: Compiler) -> list[Diagnostic]:
    return [d for d in compiler.diagnostics if d.level == DiagnosticLevel.ERROR]


def test_ir_overload_group_resolves_by_arg_type(tmp_path):
    # Both calls must resolve -- to the bool overload and the str overload
    # respectively. A wrong pick (str into the bool param, or vice versa) would
    # be a type error, so a clean compile proves type-directed dispatch across
    # the synthesised IR overload group.
    (tmp_path / "thing.ovl").write_text("")
    (tmp_path / "main.py").write_text(
        "from plug.thing import pick\n"
        "print(pick(True))\n"
        "print(pick('hi'))\n")
    reg = FrontendRegistry()
    reg.register(_OverloadPlugin({}))
    compiler = Compiler(tmp_path / "main.py", lib_dirs=_STDLIB_DIRS,
                        frontend_registry=reg)
    compiler.compile()
    assert not _errors(compiler), _errors(compiler)
