# varargs is the compiler-internal *args body view; users cannot name it in
# annotations (they write `*args: T`). Importing + using it is rejected.
from tpy._core import varargs


def f(x: varargs[int]) -> None:  # tpyc: error(/'varargs' is a compiler-internal type/)
    pass


def main() -> None:
    pass


main()
