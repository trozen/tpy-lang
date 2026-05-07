from tpy import Int32

class Public:
    val: Int32
    def __init__(self) -> None:
        self.val = Int32(1)

class Hidden:
    val: Int32
    def __init__(self) -> None:
        self.val = Int32(2)

__all__ = ["Public"]
