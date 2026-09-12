# D16 Phase 1: __getattr__ cannot return a view/borrowed type (Span/Ptr/Ref/StrView/BytesView).
from tpy import int32, Span

class Bad:
    _items: list[int32]

    def __init__(self) -> None:
        self._items = [int32(1), int32(2)]

    def __getattr__(self, name: str) -> Span[int32]:  # tpyc: error(/value type, Any, or Own/)
        return self._items
