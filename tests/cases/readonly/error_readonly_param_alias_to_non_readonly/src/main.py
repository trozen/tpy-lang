from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v


def mutate(b: Box) -> None:
    b.value = b.value + 1


@readonly
def bad(b: Box) -> None:
    alias = b
    mutate(alias)  # tpyc: error(/readonly/)
