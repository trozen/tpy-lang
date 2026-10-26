from tpy import int32


class E(Exception):
    def __init__(self, x: int32) -> None:
        super().__init__("right E")
        self.x = x


class D(Exception):
    def __init__(self) -> None:
        super().__init__("right D")
