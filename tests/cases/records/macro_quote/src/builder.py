# tpy: macro_module
"""Test macro module using quote/add_method_from_source APIs."""
from tpyc.macro_api import (
    ClassInfo, FieldInfo, class_macro, ast, types,
    Expr, Stmt, Function, Type,
)
from _macro_helpers import build_init, build_eq


@class_macro
def builder(cls: ClassInfo) -> None:
    """Macro that generates init, eq, setters, and a describe() method."""
    cls.set_match_args([f.name for f in cls.fields])
    cls.add_method(build_init(cls, [], cls.fields))
    cls.add_method(build_eq(cls, cls.fields))

    # Generate setter methods via add_method_from_source
    for field in cls.fields:
        cls.add_method_from_source(f"""
            def set_{field.name}(self, value: {field.type.name}) -> None:
                self.{field.name} = value
        """)

    # Generate a describe() method using quote_fun
    parts = [f'"{f.name}=" + str(self.{f.name})' for f in cls.fields]
    expr = ' + ", " + '.join(parts) if parts else '""'

    cls.add_method_from_source(f"""
        def describe(self) -> str:
            return {expr}
    """)

    # Generate a reset() method using ast.quote() for statements + quote_expr
    body: list[Stmt] = []
    for field in cls.fields:
        body.extend(ast.quote(f"""
            self.{field.name} = {field.type.name}()
        """))
    cls.add_method(ast.function(
        "reset", [], types.void, body, is_method=True,
    ))

    # Generate a default_x() method using quote_expr
    if cls.fields:
        f0 = cls.fields[0]
        default_expr = ast.quote_expr(f"{f0.type.name}()")
        cls.add_method(ast.function(
            "default_first", [], f0.type.raw_type,
            [ast.return_(default_expr)],
            is_method=True, is_readonly=True,
        ))

    # Generate a field_count() staticmethod using quote_fun
    count = len(cls.fields)
    func = ast.quote_fun(f"""
        def field_count() -> int32:
            return {count}
    """)
    func.is_staticmethod = True
    cls.add_method(func)
