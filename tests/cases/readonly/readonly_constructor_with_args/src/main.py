from tpy import int32, readonly


class Box:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


@readonly
def build() -> int32:
    b = Box(3)
    return b.x  # tpyc: ok


print(build())
