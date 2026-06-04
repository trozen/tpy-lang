# The internal *args view is also rejected via a qualified reference
# (`tpy.varargs`), not just a bare `from tpy import varargs`.
import tpy


def f(x: tpy.varargs[int]) -> None:  # tpyc: error(/'varargs' is a compiler-internal type/)
    pass


def main() -> None:
    pass


main()
