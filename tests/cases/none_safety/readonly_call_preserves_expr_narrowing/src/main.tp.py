from tpy import Int32, readonly


class Box:
    value: Int32 | None

    def __init__(self, v: Int32):
        self.value = v


@readonly
def observe(b: Box) -> None:
    return


def use(b: Box) -> Int32:
    if b.value is not None:
        observe(b)
        return b.value + 1  # tpyc: ok
    return 0


print(use(Box(5)))
