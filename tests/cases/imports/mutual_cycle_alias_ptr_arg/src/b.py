from __future__ import annotations
from tpy import int32

import a


class Item:
    n: int32 = 5


def count_bag(bag: a.Bag) -> int32:
    return bag.count()
