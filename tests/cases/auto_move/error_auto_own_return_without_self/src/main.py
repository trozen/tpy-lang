# auto_own[T] in return type without auto_own[Self] on self should error.
from __future__ import annotations
from tpy import Int32, auto_own

class Foo:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

    def get(self) -> auto_own[Int32]:  # tpyc: error(/auto_own/)
        return self.val
