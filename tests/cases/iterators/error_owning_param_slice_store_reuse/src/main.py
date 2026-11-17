# "An owning iterable is traversed once" (LANGUAGE_FEATURES.md): a slice
# store traverses it as `out.extend(it)` does, so a later loop is an error.
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def f(it: Iterable[Own[R]]) -> int32:
    out = [R(7)]
    out.append(R(8))
    out[0:1] = it
    n = len(out)
    for r in it:  # tpyc: error(/Cannot use 'it' after it was consumed: an owning iterable \(Iterable\[Own\[R\]\]\) is traversed once; copy it first: `xs = list\(it\)`/)
        n += r.n
    return n


def main() -> None:
    print(f([R(1), R(2)]))


main()
