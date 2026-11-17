# A dead list subclass of @nocopy elements at an owning element slot: it has no
# consuming __iter__ of its own, so it is lent and each element would be copied
# -- the remedy names no auto-move, which never applies to it.
from typing import Iterable
from tpy import Own, int32, nocopy


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


class NList(list[N]):
    pass


def take(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def main() -> None:
    a = NList()
    a.append(N(1))
    # The subject: the copy error's remedy for a container never handed over.
    print("subclass", take(a))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage \(use Own\[\.\.\.\] to transfer ownership, or copy\(\) if a copy is truly intended; 'NList' has no consuming __iter__/)


main()
