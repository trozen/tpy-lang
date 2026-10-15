from __future__ import annotations

from tpy import Ptr, ValueType


class Handle[T]:
    ptr: Ptr[T]

    def __init__(self) -> None:
        self.ptr = None


class W[T](ValueType):
    p: Ptr[T]

    def __init__(self) -> None:
        self.p = None


class Node[T]:
    children: list[Node[T]]
    p: Ptr[T]

    def __init__(self) -> None:
        self.children = []
        self.p = None
