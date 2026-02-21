from tpy import Int32


class Box:
    value: Int32 | None

    def __init__(self, value: Int32 | None):
        self.value = value


def use_after_rebind(a: Box, b: Box) -> Int32:
    local = a
    if local.value is not None:
        local = b
        return local.value + 1  # tpyc: warning(/Potential None access/)
    return 0


print(use_after_rebind(Box(2), Box(10)))
print(use_after_rebind(Box(2), Box(11)))
