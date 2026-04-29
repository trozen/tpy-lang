# tpy: macro_module
"""Builder macro that emits a record with a non-trivial method body.

Used by tests/cases/macros/builder_method_body/ to verify that pass 5.5
expansion lets synthesized record methods participate in pass-6 body
analysis. The synthesized record carries a `total()` method whose body
sums all `Int32` fields -- something codegen cannot emit without
sema-resolved expression types.
"""
from tpyc.macro_api import (
    builder_macro, builder_method, builder_terminal,
    BuilderContext, MacroArgs, TypeInfo, types, ast,
)


@builder_macro
class Counter:
    def __init__(self, ctx: BuilderContext, args: MacroArgs) -> None:
        self.values: list = []

    @builder_method
    def add(self, ctx: BuilderContext, args: MacroArgs) -> None:
        v = ctx.eval_literal_or_final(args.positional[0].expr)
        self.values.append(v)

    @builder_terminal
    def build(self, ctx: BuilderContext, args: MacroArgs) -> TypeInfo:
        if not self.values:
            ctx.error("Counter requires at least one add() before build()")

        record_name = ctx.fresh_module_name("counter")
        fields = [(f"v{i}", types.int32) for i in range(len(self.values))]

        body_expr = ast.field_access(ast.name("self"), fields[0][0])
        for fname, _ in fields[1:]:
            body_expr = ast.binop(
                body_expr, "+",
                ast.field_access(ast.name("self"), fname),
            )
        total_method = ast.function(
            "total", [], types.int32, [ast.return_(body_expr)],
            is_method=True,
        )

        record_type = ctx.emit_record(
            record_name, fields, methods=[total_method])

        ctor_args = [ast.int_lit(v) for v in self.values]
        factory_body = [ast.return_(ast.call(record_name, ctor_args))]
        fn_name = ctx.fresh_module_name("build_counter")
        ctx.emit_function(fn_name, [], record_type.raw_type, factory_body)
        ctx.replace_call(fn_name, MacroArgs())
        return record_type
