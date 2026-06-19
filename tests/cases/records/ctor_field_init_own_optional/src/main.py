# An `Own[Node | None]` ctor param read via member access in a member-init
# field initializer -- exercises the Own-pointer-repr-optional seeding branch
# (optional_locals + the var_types/local_ns rebind), distinct from a plain
# optional-ptr or union param.
from tpy import Own


class Node:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    found: int

    def __init__(self, n: Own[Node | None]) -> None:
        self.found = n.v if n is not None else -1


def main() -> None:
    print(Holder(Node(5)).found)
    print(Holder(None).found)


main()
