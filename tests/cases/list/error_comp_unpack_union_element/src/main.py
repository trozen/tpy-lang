# A comprehension whose unpack target binds a UNION element: the element target
# has no binding row, so the comprehension rejects.
from tpy import Int32


class Alpha:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Beta:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def count(pairs: list[tuple[Int32, Alpha | Beta]]) -> Int32:
    return len([u for (_, u) in pairs])  # tpyc: error(/expr.list_comp/)


def main() -> None:
    print(count([]))


main()
