# A pointer-repr `Optional[T]` local whose FIRST binding is a container LITERAL
# and which is REBOUND later. The decl takes the owning rebind slot every such
# name is registered for, and the later reseat re-points the pointer at it --
# one slot per name, so the rebind neither leaks the first binding nor
# invalidates the pointer. A literal fills the slot, so the pointer is never
# null; the sibling first binding that CAN leave it empty (an optional field
# off a dying temporary) keeps rejecting, pinned by
# `error_opt_slot_field_rebind`. The list bindings are mutated after they are
# taken, so a copy would show up as a lost element.
from tpy import int32, Own


class F:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def make(self) -> Own[list[int32]]:
        return [self.n, self.n, self.n]


class Builder:
    f: F

    def __init__(self, n: int32) -> None:
        self.f = F(n)

    # method position
    def build(self) -> int32:
        a: list[int32] | None = [1, 2]  # tpyc: ok
        if a is not None:
            a.append(3)
        a = self.f.make()
        if a is None:
            return -1
        a.append(4)
        return len(a)


# free function: literal decl, mutate, then rebind to an owned call result
def literal_position(f: F) -> int32:
    a: list[int32] | None = [1, 2]  # tpyc: ok
    if a is not None:
        a.append(3)
        print("literal: after decl", len(a))
    a = f.make()
    if a is None:
        return -1
    a.append(4)
    return len(a)


# the same local with a None arm in between: the rebind re-points the slot
def none_arm_position(f: F, c: bool) -> int32:
    a: list[int32] | None = [1, 2]  # tpyc: ok
    if c:
        a = None
    a = f.make()
    if a is None:
        return -1
    return len(a)


def main() -> None:
    f = F(7)
    # bound first: the function prints, and a print argument that prints
    # interleaves ahead of the earlier arguments
    # (BUGS.md#subexpression-right-to-left-eval)
    got = literal_position(f)
    print("literal:", got)
    print("method:", Builder(7).build())
    print("none arm:", none_arm_position(f, True), none_arm_position(f, False))


main()
