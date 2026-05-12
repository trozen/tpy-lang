# __all__ must be a compile-time literal (list / tuple / set of string
# literals). Captured at parse time on every module, regardless of
# whether anyone star-imports it. This case verifies the parse-time
# rejection.
_base = ["foo"]

__all__ = _base + ["bar"]  # tpyc: error(/compile-time literal/)


def foo() -> None:
    pass


def bar() -> None:
    pass
