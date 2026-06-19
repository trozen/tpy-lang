# `None` in an inline-union isinstance second arg is rejected (a NoneType
# check member does not compose with the union-narrowing codegen yet; see
# BUGS.md). CPython accepts `A | None`; narrow None with `x is None` instead.
class A:
    def __init__(self) -> None:
        pass


def f(v: A | None) -> None:
    if isinstance(v, A | None):  # tpyc: error(/None.*inline union/)
        print("a")


def main() -> None:
    pass


main()
