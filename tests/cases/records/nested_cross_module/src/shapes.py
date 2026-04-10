# Nested types exported for cross-module use
from tpy import Int32
from enum import Enum, auto

class Container:
    class Kind(Enum):
        A = auto()
        B = auto()

    class Inner:
        val: Int32
        def __init__(self, val: Int32) -> None:
            self.val = val

    kind: Kind
    def __init__(self, kind: Kind) -> None:
        self.kind = kind
