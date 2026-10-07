# A nullable borrow-tuple LOCAL (the call's `std::optional<std::tuple<Box*,
# int32_t>>`) placed in a container literal rejects: the storage element
# has no lift for that layout (the call-result twin rejects the same way).
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def maybe(b: Box, k: bool) -> tuple[Box, int32] | None:
    if k:
        return (b, 1)
    return None


def main() -> None:
    b = Box(1)
    r = maybe(b, True)
    xs = [r]  # tpyc: error(/not yet supported/)
    print(len(xs))


main()
