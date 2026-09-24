# A name rebound in a loop from a call on a mixed ternary receiver whose
# result borrows the receiver: the name outlives the iteration the fresh
# arm's slot lives in, so a receiver whose method may return a borrow of it
# is refused (BUGS.md#reference-ternary-position-gaps).
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def me(self) -> "C":
        return self


def make(k: int32) -> Own[C]:
    return C(k)


def lp() -> None:
    a = C(1)
    for i in range(3):
        d = (a if i == 0 else make(i * 10)).me()  # tpyc: warning(/borrows from temporary receiver/) error(/method\.recv\.select_slot_lend/)
        d.n += 1
    print(a.n, d.n)


def main() -> None:
    lp()


main()
