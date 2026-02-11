from tpy import Int32, readonly


class Box:
    value: Int32

    def __init__(self, v: Int32):
        self.value = v


@readonly
def bad(b: Box) -> None:
    b.value = 1  # tpyc: error(/Cannot assign to fields inside @readonly function/)
