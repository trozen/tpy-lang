# `set.add` is an Own-coerced slot like `list.append`, so a borrow-returning
# call arriving there copies with the same warning. A frozen dataclass is the
# hashable payload; the copy diverges from CPython (which stores the very
# object), so the stored element is deliberately not observed after mutating
# the source.
from dataclasses import dataclass

from tpy import Int32


@dataclass(frozen=True)
class Key:
    n: Int32


class Holder:
    k: Key

    def __init__(self) -> None:
        self.k = Key(7)

    def borrow(self) -> Key:
        return self.k


def main() -> None:
    h = Holder()
    ks: set[Key] = set()
    ks.add(h.borrow())  # tpyc: warning(/copies Key into owned storage/)
    print(len(ks))


main()
