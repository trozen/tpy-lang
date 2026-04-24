# A pre-loop narrowing must not survive if the body reassigns the name to
# a value that reintroduces the removed type.


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


def clobber_narrowing(seed: Point | None, fallback: Point) -> Point:
    x = seed
    if x is None:
        return fallback
    for _ in range(3):
        x = None
    return x  # tpyc: error(/Type mismatch/)
