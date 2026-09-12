# Parent/child with `Weak[parent]` link breaks the reference cycle:
# acyclic-strong-only would leak (see rc_cycle_leak); using Weak for the
# back-edge lets the chain drop cleanly when the root Rc is released.
from __future__ import annotations
from tpy import int32
from tplib.rc import Rc, Weak


class Node:
    name: str
    # Strong forward edge owns the child; weak back edge avoids the cycle.
    parent: Weak[Node] | None
    children: list[Rc[Node]]

    def __init__(self, name: str) -> None:
        self.name = name
        self.parent = None
        self.children = []
        print("init", name)

    def __del__(self) -> None:
        print("del", self.name)


def main() -> None:
    root = Rc.new(Node("root"))
    child = Rc.new(Node("child"))

    # Wire the cycle: root owns child strongly; child holds a Weak back to root.
    root.get().children.append(child.clone())
    child.get().parent = root.downgrade()

    # Walk back via Weak to prove the link is alive.
    parent_ref = child.get().parent
    if parent_ref is not None:
        upgraded = parent_ref.upgrade()
        if upgraded is not None:
            print("found parent:", upgraded.get().name)

    # Function returns: child drops, then root drops. root's strong=0 triggers
    # payload destruction, which drops root's children list (releasing the
    # surviving Rc on child), which destructs child's payload, which drops
    # child's `parent` Weak. No cycle, no leak.


main()
print("done")
