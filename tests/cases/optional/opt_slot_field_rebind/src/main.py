# A pointer-repr `Optional[T]` local whose FIRST binding is an optional FIELD
# read off a dying temporary may be REBOUND. The whole optional materializes
# into a slot at the decl, so the pointer is null whenever that field was None;
# the rebind therefore has to take the name's own rebind slot and re-point the
# pointer (`p = &*(__slot = Point(..))`) instead of writing through it. Sema's
# alias-rebind pass answers that in-place question from the local's DECLARED
# slot type, so a may-be-empty Optional never reaches the in-place form.
# Every rebind position below is straight-line-declared first; the BRANCH-FIRST
# decl of the same source is a located reject in its own right
# (`reseat.branch_rvalue_source`, pinned by `error_branch_first_opt_field_slot`)
# because the hoist asks the reseat's rvalue-shape list instead of this slot
# verdict.
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


# free function: the source field is empty for x <= 0, non-empty otherwise;
# both then rebind and read the new object back
def rebound(x: int32) -> str:
    p: Point | None = make_holder(x).value  # tpyc: ok
    first = "none" if p is None else p.tag
    p = Point("set")
    p.tag = p.tag + "!"
    return first + "/" + p.tag


# method position, with a None arm between the decl and the rebind
class Driver:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def run(self) -> str:
        p: Point | None = make_holder(self.x).value  # tpyc: ok
        p = None
        p = Point("again")
        return p.tag


# branch position: the rebind runs on one path only, so the pointer still has
# to survive the path that skipped it
def branch_rebound(x: int32) -> str:
    p: Point | None = make_holder(x).value  # tpyc: ok
    if x > 1:
        p = Point("set")
    return "none" if p is None else p.tag


def main() -> None:
    print("empty:", rebound(0))
    print("non-empty:", rebound(3))
    print("none arm:", Driver(0).run(), Driver(3).run())
    print("branch:", branch_rebound(0), branch_rebound(1), branch_rebound(3))


main()
