# `x is None` on an operand of a generic type parameter is not supported yet
# (BUGS.md#identity-generic-param-rejects); CPython answers per argument.
class A:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def is_none[T](x: T) -> bool:
    return x is None  # tpyc: error(/an operand of a generic type parameter is not supported yet/)


o: A | None = None
print(is_none(o), is_none(A(1)))
