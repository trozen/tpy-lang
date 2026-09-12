from tpy import int32


class Box:
    value: int32 | None

    def __init__(self, v: int32):
        self.value = v


def opaque(b: Box) -> None:
    b.value = b.value


def use_after_call(b: Box) -> int32:
    while b.value is not None:
        opaque(b)
        return b.value + 1  # tpyc: warning(/Potential None access/)
    return 0


print(use_after_call(Box(5)))
