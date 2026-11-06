# docs/LANGUAGE_FEATURES.md "An owning iterable is traversed once": an exception may
# leave a `try` body after its traversal, so a handler's traversal is a re-read.
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


def retry(it: Iterable[Own[R]]) -> int32:
    try:
        n = total(it)
        if n > 0:
            raise ValueError("again")
        return n
    except ValueError:
        return total(it)  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable/)


def main() -> None:
    print(retry([R(1), R(2)]))


main()
