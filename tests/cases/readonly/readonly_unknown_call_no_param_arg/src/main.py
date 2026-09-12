# @readonly: passing a locally-constructed object (not param-rooted) to a
# non-readonly function is allowed.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v


def mutate(b: Box) -> None:
    b.value = b.value + 1


@readonly
def ok(b: Box) -> int32:
    local = Box(b.value)
    mutate(local)  # tpyc: ok
    return local.value


print(ok(Box(5)))
