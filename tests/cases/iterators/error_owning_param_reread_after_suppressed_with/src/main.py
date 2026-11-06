# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": a suppressing
# `with` resumes after itself from wherever its body raised, so a traversal in the body
# may have run when the code after the statement reads the parameter again.
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def total(it: Iterable[Own[R]]) -> int32:
    n = 0
    for r in it:
        n += r.n
    return n



class Quiet:
    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> bool:
        return True


def f(it: Iterable[Own[R]]) -> int32:
    n = 0
    with Quiet():
        n += total(it)
        raise ValueError("stop")
    return n + total(it)  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable/)


def main() -> None:
    print(f([R(1), R(2)]))


main()
