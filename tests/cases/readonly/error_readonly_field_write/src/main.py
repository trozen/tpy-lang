from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v


@readonly
def bad(b: Box) -> None:
    b.value = 1  # tpyc: error(/Cannot mutate readonly reference/)
