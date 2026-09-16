# The ctor member-init position of `list/error_container_elem_borrow_call`: a
# BORROW-returning callee (a bare `-> list[int32]`, no `Own`) at the element
# slot of a member-init comprehension. The result aliases caller-durable
# storage, so the owning element slot would copy it silently -- sema warns and
# the element keeps rejecting. Its own case because a member-init reject fails
# the whole CONSTRUCTOR rather than demoting to the body.
from tpy import int32

SHARED: list[int32] = [1]


def borrow_rows(n: int32) -> list[int32]:
    return SHARED


class Grid:
    rows: list[list[int32]]

    def __init__(self, n: int32) -> None:
        self.rows = [borrow_rows(i) for i in range(n)]  # tpyc: warning(/copies list/) error(/not yet supported/)


def main() -> None:
    g = Grid(2)
    print(len(g.rows))


main()
