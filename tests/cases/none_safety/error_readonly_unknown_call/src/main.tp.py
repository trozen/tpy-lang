from tpy import Int32, readonly


class Box:
    value: Int32

    def __init__(self, v: Int32):
        self.value = v


def mutate(b: Box) -> None:
    b.value = b.value + 1


@readonly
def bad(b: Box) -> None:
    mutate(b)  # tpyc: error(/Call to non-readonly or unknown-effect function 'mutate' is not allowed in @readonly function/)
