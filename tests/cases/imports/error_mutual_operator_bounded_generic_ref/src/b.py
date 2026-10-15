from tpy import int32
import a


class Item:
    def __init__(self, n: int32) -> None:
        self.n = n


def back() -> int32:
    return a.run()
