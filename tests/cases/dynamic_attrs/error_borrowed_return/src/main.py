# D16 Phase 1: __getattr__ cannot return a view/borrowed type (Span/Ptr/Ref/StrView/BytesView).
from tpy import Int32, Span

class Bad:
    _items: list[Int32]

    def __init__(self) -> None:
        self._items = [Int32(1), Int32(2)]

    def __getattr__(self, name: str) -> Span[Int32]:  # tpyc: error(/value type, Any, or Own/)
        return self._items
