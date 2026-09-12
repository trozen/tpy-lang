# A comprehension whose unpack target binds a UNION element: the element target
# has no binding row, so the comprehension rejects.
from tpy import int32


class Alpha:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Beta:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


def count(pairs: list[tuple[int32, Alpha | Beta]]) -> int32:
    return len([u for (_, u) in pairs])  # tpyc: error(/expr.list_comp/)


def main() -> None:
    print(count([]))


main()
