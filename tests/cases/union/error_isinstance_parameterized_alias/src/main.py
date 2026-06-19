# A non-generic alias of a parameterized generic (`JObj = dict[str, int]`)
# is rejected for isinstance, mirroring CPython, where
# `isinstance(x, dict[str, int])` raises "cannot be a parameterized generic"
# at runtime. The bare `dict` is the supported spelling.
type JObj = dict[str, int]


def f(x: int | dict[str, int]) -> None:
    if isinstance(x, JObj):  # tpyc: error(/container generic alias 'JObj'/)
        print("obj")


def main() -> None:
    v: int | dict[str, int] = 5
    f(v)


main()
