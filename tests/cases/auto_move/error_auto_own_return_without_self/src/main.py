# auto_own[T] in return type without auto_own[Self] on self should error.
from __future__ import annotations
from tpy import int32, auto_own

class Foo:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val

    def get(self) -> auto_own[int32]:  # tpyc: error(/auto_own/)
        return self.val
