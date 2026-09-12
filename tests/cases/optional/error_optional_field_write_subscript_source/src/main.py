# A whole-Optional SUBSCRIPT as the source of an optional field write: only
# field sources are admitted, an optional element read has no arm.
# Concretely, `b.value = xs[0]` where `xs: list[int32 | None]`; TPy rejects
# that assignment today.
from tpy import int32


class Box:
    value: int32 | None

    def __init__(self, v: int32) -> None:
        self.value = v


def put(b: Box, xs: list[int32 | None]) -> None:
    # The source is an optional container element, not a field.
    b.value = xs[0]  # tpyc: error(/stmt\.assign:subscript\.elem\.optional/)


def main() -> None:
    b = Box(1)
    xs: list[int32 | None] = [7]
    put(b, xs)
    print(b.value)


main()
