# A module's `__all__` that is not a literal compiles when a later `=` replaces
# it with one: the parser reads each `__all__` statement once.
from lit import *


def f() -> int:
    return 1


__all__ = list()  # tpyc: ok
__all__ = ["f"]  # tpyc: ok


def main() -> None:
    # The neighbour: a plain literal `__all__`, star-imported.
    print("overwritten:", f(), g())
