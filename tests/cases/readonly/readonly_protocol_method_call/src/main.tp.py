# @readonly: calling len() on a Sized protocol param is allowed (Sized is readonly).
from tpy import Int32, readonly
from typing import Sized


@readonly
def get_len(s: Sized) -> Int32:
    return len(s)  # tpyc: ok


items: list[Int32] = []
items.append(1)
items.append(2)
items.append(3)
print(get_len(items))
