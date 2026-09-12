from tpy import int32


class Box:
    value: int32 | None

    def __init__(self, value: int32):
        self.value = value


def stale_after_rebind(b: Box, other: Box) -> int32:
    local: Box = b
    while local.value is not None:
        local = other
        if 0 == 1:
            return local.value + 1  # tpyc: warning(/Potential None access/)
        break
    return 0


print(stale_after_rebind(Box(5), Box(3)))
