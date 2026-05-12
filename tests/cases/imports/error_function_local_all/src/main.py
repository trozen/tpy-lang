# A function-local `__all__ = []` is not module metadata -- the empty
# list still needs a type. The empty-`__all__` shortcut that infers
# `list[str]` applies only at module scope. Intentionally no use of
# the list below: an actual use would let generic inference deduce
# the element type from context, which would mask the scope rule.
def f() -> None:
    __all__ = []  # tpyc: error(/Cannot infer element type/)


f()
