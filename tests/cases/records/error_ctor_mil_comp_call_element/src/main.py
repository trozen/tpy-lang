# A member-init comprehension keeps its own element rules: a CALL at a
# container element slot has no vetted spelling, and a member-init reject
# fails the whole constructor rather than demoting to the body.
from tpy import int32, Own


def mk(i: int32) -> Own[list[int32]]:
    return [i]


class Grid:
    rows: list[list[int32]]

    def __init__(self, n: int32) -> None:
        self.rows = [mk(i) for i in range(n)]  # tpyc: error(/comp.container_value/)


def main() -> None:
    g = Grid(2)
    print(len(g.rows))


main()
