from tpy import int32


class Box:
    value: int32 | None

    def __init__(self, value: int32 | None):
        self.value = value


def opaque(b: Box) -> None:
    print(0)


def use_after_call(b: Box) -> int32:
    if b.value is not None:
        opaque(b)
        return b.value + 1  # tpyc: warning(/Potential None access/)
    return 0


print(use_after_call(Box(4)))
