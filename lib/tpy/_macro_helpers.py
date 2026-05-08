# tpy: macro_module
"""Shared helpers for TurboPython macro modules.

Provides build_* functions for synthesizing common dunder methods
(__init__, __eq__, __repr__, __hash__, ordering). Used by @dataclass,
@model, and other class macros.
"""

from tpyc.macro_api import (
    ClassInfo, FieldInfo, ast, types, Expr, Stmt, Function, Type,
)


# Rich-comparison dunders in CPython's documented anchor-priority
# order, paired with the corresponding binary operator. Used by
# `build_order` (synthesize all four for `@dataclass(order=True)`)
# and `@total_ordering` (pick the first defined op as anchor).
ORDER_DUNDERS = ("__lt__", "__le__", "__gt__", "__ge__")
ORDER_DUNDER_OPS = ("<", "<=", ">", ">=")


def build_init(
    cls: ClassInfo,
    parent_fields: list[FieldInfo],
    own_fields: list[FieldInfo],
) -> Function:
    """Build a synthetic __init__ method from field lists."""
    params: list[tuple[str, Type]] = []
    defaults: list[Expr | None] = []
    body: list[Stmt] = []

    # Parent fields come first; forwarded via super().__init__()
    for fld in parent_fields:
        param_type = fld.type.raw_type
        if not param_type.is_value_type():
            param_type = types.own(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)

    if parent_fields:
        super_args = [ast.name(fld.name) for fld in parent_fields]
        super_call = ast.method_call(
            ast.call("super"), "__init__", super_args,
        )
        body.append(ast.expr_stmt(super_call))

    # Own fields
    for fld in own_fields:
        param_type = fld.type.raw_type
        if not param_type.is_value_type():
            param_type = types.own(param_type)
        params.append((fld.name, param_type))
        defaults.append(fld.default_expr)
        body.append(ast.assign(
            ast.field_access(ast.name("self"), fld.name),
            ast.name(fld.name),
        ))

    return ast.function(
        "__init__", params, types.void, body,
        is_method=True, defaults=defaults,
    )


def build_eq(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build a synthetic __eq__ method from field list."""
    other_type = types.named(cls.name)
    comparisons = [
        ast.binop(
            ast.field_access(ast.name("self"), fld.name),
            "==",
            ast.field_access(ast.name("other"), fld.name),
        )
        for fld in all_fields
    ]
    if not comparisons:
        return ast.function(
            "__eq__", [("other", other_type)], types.bool, [ast.return_(ast.bool_lit(True))],
            is_method=True,
        )
    eq_expr: Expr = comparisons[0]
    for cmp in comparisons[1:]:
        eq_expr = ast.binop(eq_expr, "&&", cmp)

    return ast.function(
        "__eq__", [("other", other_type)], types.bool, [ast.return_(eq_expr)],
        is_method=True,
    )


def build_repr(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build a synthetic __repr__ method from field list.

    Generates: f"ClassName(field1={repr(self.field1)}, field2={repr(self.field2)})"
    """
    parts = ", ".join(
        f"{fld.name}={{repr(self.{fld.name})}}" for fld in all_fields
    )
    source = f'f"{cls.name}({parts})"'
    return ast.function(
        "__repr__", [], types.str,
        [ast.return_(ast.quote_expr(source))],
        is_method=True, is_readonly=True,
    )


def build_hash(cls: ClassInfo, all_fields: list[FieldInfo]) -> Function:
    """Build a synthetic __hash__ method from field list.

    XORs hash(field) values. Simple but has known weaknesses (symmetric
    collisions, self-cancellation). Good enough until tuple hashing lands,
    at which point this becomes hash((self.f1, self.f2, ...)).
    """
    body: list[Stmt] = []

    first = all_fields[0]
    body.append(ast.var_decl(
        "h", types.uint64,
        ast.call("hash", [ast.field_access(ast.name("self"), first.name)]),
    ))

    for fld in all_fields[1:]:
        hash_call = ast.call("hash", [ast.field_access(ast.name("self"), fld.name)])
        body.append(ast.assign(
            ast.name("h"),
            ast.binop(ast.name("h"), "^", hash_call),
        ))

    body.append(ast.return_(ast.name("h")))
    return ast.function(
        "__hash__", [], types.uint64, body,
        is_method=True, is_readonly=True,
    )


def build_order(cls: ClassInfo, all_fields: list[FieldInfo]) -> list[Function]:
    """Build synthetic ordering methods (__lt__, __le__, __gt__, __ge__).

    Uses field-by-field chained comparison (lexicographic order).
    """
    other_type = types.named(cls.name)
    results: list[Function] = []
    for dunder, op in zip(ORDER_DUNDERS, ORDER_DUNDER_OPS):
        body: list[Stmt] = []
        for fld in all_fields[:-1]:
            sf = ast.field_access(ast.name("self"), fld.name)
            of = ast.field_access(ast.name("other"), fld.name)
            body.append(ast.if_(
                ast.binop(sf, "!=", of),
                [ast.return_(ast.binop(sf, op, of))],
            ))
        last = all_fields[-1]
        sf = ast.field_access(ast.name("self"), last.name)
        of = ast.field_access(ast.name("other"), last.name)
        body.append(ast.return_(ast.binop(sf, op, of)))

        results.append(ast.function(
            dunder, [("other", other_type)], types.bool, body,
            is_method=True, is_readonly=True,
        ))
    return results
