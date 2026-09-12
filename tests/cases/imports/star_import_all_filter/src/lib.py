from tpy import int32

class Public:
    val: int32
    def __init__(self) -> None:
        self.val = int32(1)

class Hidden:
    val: int32
    def __init__(self) -> None:
        self.val = int32(2)

__all__ = ["Public"]
