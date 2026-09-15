# A `Callable` binding invoked under a name that ALSO names a module function:
# sema resolves the binding, so the render is the bare name, never the module
# function's qualified spelling. The differing-arity section is the one that
# would fail loudly if the callee were re-derived from the name.
from typing import Callable
from tpy import int32


def f(x: int32) -> int32:
    return x + 1


def wide(x: int32, y: int32) -> int32:
    return x * 100 + y


def double(x: int32) -> int32:
    return x * 2


# free function: the param shadows a same-arity module function.
def same_arity(f: Callable[[int32], int32], y: int32) -> int32:
    return f(y)  # tpyc: ok


# free function: the param shadows a WIDER module function; calling the module
# one here would need two arguments.
def diff_arity(wide: Callable[[int32], int32], y: int32) -> int32:
    return wide(y)  # tpyc: ok


# free function: a LOCAL callable binding shadowing the same names.
def local_binding(y: int32) -> int32:
    f: Callable[[int32], int32] = double
    return f(y)  # tpyc: ok


class Runner:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same shadowing param inside a record method body.
    def run(self, f: Callable[[int32], int32], y: int32) -> int32:
        return f(y)  # tpyc: ok


def main() -> None:
    print("same", same_arity(double, 4), f(4))
    print("diff", diff_arity(double, 4), wide(1, 2))
    print("local", local_binding(5))
    print("method", Runner("r").run(double, 6))


main()
