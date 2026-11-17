# docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage": a lent element
# (`ll[0]`) at an owning element slot copies each element, so a `@nocopy`
# element refuses it, as a lent name of one does.
from typing import Iterable
from tpy import Own, int32, nocopy


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def take(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def main() -> None:
    ll = [[N(1)], [N(2)]]
    print(take(ll[0]))  # tpyc: error(/cannot copy non-copyable type 'N'/)


main()
