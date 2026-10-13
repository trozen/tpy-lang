# A rebound pointer tuple as a container literal's element: its whole copy
# copies what it owns (BUGS.md#tuple-local-last-use-copies-owned-elements),
# so it is refused.
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def collect(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    xs = [p]  # tpyc: error(/expr.container_literal/)
    print(len(xs))


collect(Box(5))
