# A generic record with a user __span__ (delegating to a backing ArrayList),
# in a module SEPARATE from main. Its cross-module resolution is what
# regressed: span-coercion at the call site needs Pool's record by qname.
from tpy import Own, Span, Spannable, readonly, span
from tplib.array_list import ArrayList


class Pool[T](Spannable[T]):
    _items: ArrayList[T, 8]

    def __init__(self, items: Own[list[T]]) -> None:
        self._items = ArrayList[T, 8](items)

    def __span__(self) -> Span[readonly[T]]:
        return span(self._items)


def make_pool[T](items: Own[list[T]]) -> Own[Pool[T]]:
    return Pool[T](items)
