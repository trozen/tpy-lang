# Nested types exported for cross-module use
from tpy import int32
from enum import Enum, auto

class Container:
    class Kind(Enum):
        A = auto()
        B = auto()

    class Inner:
        val: int32
        def __init__(self, val: int32) -> None:
            self.val = val

    kind: Kind
    def __init__(self, kind: Kind) -> None:
        self.kind = kind
