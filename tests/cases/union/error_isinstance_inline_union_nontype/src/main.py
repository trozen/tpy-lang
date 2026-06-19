# An inline-union isinstance with a non-type operand (`A | 5`) is rejected
# (a parameterized generic like `A | list[int]` is rejected earlier, at parse).
class A:
    def __init__(self) -> None:
        pass


def f(v: A | int) -> None:
    if isinstance(v, A | 5):  # tpyc: error(/inline-union members must be type names/)
        print("x")


def main() -> None:
    pass


main()
