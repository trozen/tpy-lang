# A `+=` onto a non-literal `__all__` compiles when a later `=` replaces both
# with a literal.
__all__ = list()  # tpyc: ok
__all__ += ["f"]  # tpyc: ok
__all__ = ["f"]  # tpyc: ok


def f() -> int:
    return 1


def main() -> None:
    print("dynamic then literal:", f())
