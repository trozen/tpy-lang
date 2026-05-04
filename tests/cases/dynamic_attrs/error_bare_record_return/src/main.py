# D16 Phase 1: __getattr__ cannot return a bare non-value record; use Own[T] instead.
from tpy import Int32

class Box:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

class Bad:
    _b: Box

    def __init__(self, b: Box) -> None:
        self._b = b

    def __getattr__(self, name: str) -> Box:  # tpyc: error(/value type, Any, or Own/)
        return self._b
