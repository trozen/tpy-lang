# tpy: macro_module
"""Smoke-test macro module exercising builder-trace infrastructure.

A trivial @builder_macro for collecting (name, value) string entries and
synthesizing a record + factory function on the terminal call. Used by
tests/cases/macros/builder_smoke/ to validate Phase 7 wiring without
depending on argparse semantics.
"""

from tpyc.macro_api import (
    builder_macro, builder_method, builder_returns, builder_terminal,
    BuilderContext, MacroArgs, TypeInfo, types, ast,
)


@builder_macro
class Section:
    """Minimal sub-builder used only to exercise @builder_returns
    diagnostic paths. No positive test currently invokes the terminal,
    so commit() is a stub that would error if reached.
    """
    def __init__(self, ctx: BuilderContext, args: MacroArgs) -> None:
        pass

    @builder_terminal
    def commit(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        ctx.error("Section.commit is not implemented (test stub)")


@builder_macro
class Config:
    def __init__(self, ctx: BuilderContext, args: MacroArgs) -> None:
        self.entries: list = []  # list of (name, value)

    @builder_method
    def add(self, ctx: BuilderContext, args: MacroArgs) -> None:
        name = ctx.positional_str(args, 0)
        value = ctx.positional_str(args, 1)
        self.entries.append((name, value))

    @builder_returns(Section)
    def section(self, ctx: BuilderContext, args: MacroArgs):
        return Section(ctx, MacroArgs())

    @builder_terminal
    def build(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        record_name = ctx.fresh_module_name("config")
        fields = [(name, types.str_view) for name, _ in self.entries]
        record_type = ctx.emit_record(record_name, fields)

        ctor_args = [ast.str_lit(value) for _, value in self.entries]
        body = [ast.return_(ast.call(record_name, ctor_args))]

        fn_name = ctx.fresh_module_name("build_config")
        ctx.emit_function(fn_name, [], record_type.raw_type, body)
        ctx.replace_call(fn_name, MacroArgs())
        return record_type
