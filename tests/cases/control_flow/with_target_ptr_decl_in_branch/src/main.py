# A REASSIGNED `with` as-target binds a fresh pointer local (`Slot* r = &...`),
# and that decl now lands inside a branch or loop body as well as at function
# top. The manager mutates through the borrow after the block, so a silent copy
# of the target would print the pre-mutation value.
from tpy import Int32


class Slot:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Res:
    s: Slot

    def __init__(self, v: Int32) -> None:
        self.s = Slot(v)

    def __enter__(self) -> Slot:
        return self.s

    def __exit__(self, et, ev, tb) -> None:
        print("exit", self.s.v)


def in_branch(flag: bool) -> None:
    if flag:
        # First bind of a reassigned target INSIDE the branch: the pointer
        # local is declared here, not at function top.
        with Res(1) as r:
            r.v += 10
        with Res(2) as r:
            r.v += 20


def in_loop() -> None:
    for i in range(2):
        # Same first bind, re-declared per iteration.
        with Res(i) as r:
            r.v += 100
        with Res(i + 5) as r:
            r.v += 200


def main() -> None:
    in_branch(True)
    in_branch(False)
    in_loop()


main()
