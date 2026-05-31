# Error: a generator expression producing a freshly-constructed reference element
# would hand it out as a dangling borrow (the val_or_ref slot would point at a
# destroyed temporary). Genexprs have no Own[] surface, so the fix is the list
# comprehension form, which materializes owned storage.
class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def main() -> None:
    for b in (Node(x) for x in range(3)):  # tpyc: error(/freshly-constructed 'Node' from a generator expression.*list comprehension/)
        print(b.val)


main()
