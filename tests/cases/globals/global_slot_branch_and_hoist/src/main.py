# Module-init global-slot writes inside an if/else (each branch owns its `static`
# slot, and a second write in the SAME branch reuses that branch's slot) and to a
# HOISTED global -- the variable is always rebindable, but its backing STORAGE
# changes form once a second global name aliases it: the slot becomes a
# rebindable optional, and every write lifts through that one slot. Plus the pass-through write of a
# borrow-returning Optional call AFTER a slot-allocating write in the same
# branch: it assigns the `T*` bare and leaves the slot alone.
from tpy import Int32


class Node:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def find(ns: list[Node], k: Int32) -> Node | None:
    for n in ns:
        if n.x == k:
            return n
    return None


flag = True
g: Node | None = None
if flag:
    # The branch write allocates its own slot, scoped to this branch.
    g = Node(1)  # tpyc: ok
    # A second write in the SAME branch reuses that slot rather than adding one,
    # so it overwrites in place any alias taken between the two writes -- CPython
    # rebinds and keeps the old object alive (the rebind-in-place family in
    # BUGS.md). Nothing aliases `g` here, so the two agree.
    g = Node(2)  # tpyc: ok
else:
    # The sibling branch cannot see the other's slot -- the static is declared
    # inside that block, plain C++ scoping -- so it allocates its own. A slot
    # declared at function top could serve both arms, as the hoisted global's
    # does; one extra static is the price of not doing that yet.
    g = Node(3)  # tpyc: ok
if g is not None:
    print(g.x)
    g.x += 10
    print(g.x)

V = Node(3)
# Binding a second name to V makes V hoisted: its slot becomes an optional.
S: Node = V  # tpyc: warning(/will not keep the object it was given/)
print(S.x)
# The rebind lifts through the SAME hoisted slot, not a second one.
V = Node(4)  # tpyc: ok
print(V.x)

pool = [Node(5), Node(6)]
q: Node | None = None
if flag:
    # A slot-allocating write ...
    q = Node(7)  # tpyc: ok
    # ... then a borrow-returning Optional call: the `T*` passes through bare
    # and the slot allocated above stays untouched.
    q = find(pool, 5)  # tpyc: ok
if q is not None:
    # The mutation must reach the pool element, proving the pass-through
    # aliased it rather than reseating a copy into the slot.
    q.x += 100
print(pool[0].x)
