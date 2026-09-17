# A pointer-repr `Optional[T]` local FIRST bound inside a branch, from an
# optional FIELD read off a dying temporary. The straight-line decl of the same
# source compiles (`opt_slot_field_rebind`), and so does every REBIND of it
# inside an `if` / loop / `with` / `try`; only the branch-first decl rejects,
# because the hoist routes the source through the reseat's rvalue-shape list
# instead of the slot verdict the straight-line decl took
# (BUGS.md#branch-first-opt-field-rvalue-source-rejected, which carries the
# whole sibling table).
from tpy import int32, Own


class Point:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag


class Holder:
    value: Point | None

    def __init__(self, x: int32) -> None:
        self.value = None
        if x > 0:
            self.value = Point("live")


def make_holder(x: int32) -> Own[Holder]:
    return Holder(x)


def branch_first(x: int32) -> str:
    if x > 1:
        # the subject: the name's FIRST binding, read after the statement
        p: Point | None = make_holder(x).value  # tpyc: error(/reseat.branch_rvalue_source/)
    else:
        p = None
    return "none" if p is None else p.tag


def main() -> None:
    print("branch first:", branch_first(0), branch_first(3))


main()
