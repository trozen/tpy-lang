from tpy import Int32


class Box:
    value: Int32 | None

    def __init__(self, value: Int32):
        self.value = value


def next_value(b: Box) -> Int32:
    if b.value is not None:
        return b.value + 1  # tpyc: ok
    return 0


b1 = Box(5)
b2 = Box(0)
b2.value = None
print(next_value(b1))
print(next_value(b2))
