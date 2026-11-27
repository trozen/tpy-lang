# A body-less producer's fresh result at a protocol parameter takes one form
# whether the producer is spelled bare or module-qualified.
from typing import Iterable
from tpy import int32
import bind
from bind import make_vec


def total(xs: Iterable[int32]) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def total_l(xs: list[int32]) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def main() -> None:
    # bare and qualified spellings render the same inline argument
    print("bare", total(make_vec()), total_l(make_vec()))  # tpyc: ok
    print("qual", total(bind.make_vec()), total_l(bind.make_vec()))  # tpyc: ok
    # in a conditional, as an operand of a select
    print("cond", total(bind.make_vec() if len(bind.make_vec()) > 1 else [1, 2]))  # tpyc: ok


main()
