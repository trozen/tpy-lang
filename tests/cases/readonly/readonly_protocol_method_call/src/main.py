# @readonly: calling len() on a Sized protocol param is allowed (Sized is readonly).
from tpy import int32, readonly
from typing import Sized


@readonly
def get_len(s: Sized) -> int32:
    return len(s)  # tpyc: ok


items: list[int32] = []
items.append(1)
items.append(2)
items.append(3)
print(get_len(items))
