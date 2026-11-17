# An `__all__` bound to None and then to a literal compiles: only the value
# that stands at the end must be a literal.
__all__ = None  # tpyc: ok
__all__ = ["f"]  # tpyc: ok


def f() -> int:
    return 1


def main() -> None:
    print("none then literal:", f())
