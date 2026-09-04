# A non-value global first written inside a module-level WHILE body. The backing
# slot would keep `static` (only a for body drops it), so it would initialize on
# the first iteration and freeze that value for every later one.
from tpy import Int32


class Node:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


g: Node | None = None
i = 0
while i < 3:
    g = Node(i)  # tpyc: error(/not yet supported.*global_slot_branch/)
    if g is not None:
        print(g.x)
    i += 1
