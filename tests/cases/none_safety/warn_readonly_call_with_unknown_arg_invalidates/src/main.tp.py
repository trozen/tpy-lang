from tpy import Int32, readonly


class Box:
    value: Int32 | None

    def __init__(self, value: Int32):
        self.value = value


def mutate_and_get(b: Box) -> Int32:
    if b.value is not None:
        b.value = b.value + 1
        return b.value + 0
    return 0


@readonly
def observe(_: Int32) -> None:
    return


def use(b: Box) -> Int32:
    if b.value is not None:
        observe(mutate_and_get(b))
        return b.value + 1  # tpyc: warning(/Potential None access/)
    return 0


print(use(Box(4)))
