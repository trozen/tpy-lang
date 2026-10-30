# A @nocopy field of a local that is read again after the owning sink cannot
# move (its root does not die at the read) and has no copy to make.
from tpy import Own, int32, nocopy


@nocopy
class NB:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    def __init__(self, box: Own[NB]) -> None:
        self.box = box


def take(nb: Own[NB]) -> int32:
    return nb.n


def main() -> None:
    h = H(NB(1))
    # the root is read again below: no move, and no copy of a @nocopy leaf
    print(take(h.box))  # tpyc: error(/cannot copy non-copyable type .NB. into owned storage/)
    print(h.box.n)


main()
