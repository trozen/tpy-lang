# Own[T] inside a FIELD annotation is rejected: Own marks ownership transfer
# and is valid only in parameter and return types. A field owns its value
# regardless, so the Own is redundant -- drop it.
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


class Holder:
    b: Own[Box]  # tpyc: error(/Own\[T\] is redundant in this field type/)

    def __init__(self, b: Box):
        self.b = b


def main() -> None:
    h = Holder(Box(5))
    print(h.b.val)


main()
