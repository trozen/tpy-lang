from tpy import Int32, readonly


class Box:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


@readonly
def build() -> Int32:
    b = Box(3)
    return b.x  # tpyc: ok


print(build())
