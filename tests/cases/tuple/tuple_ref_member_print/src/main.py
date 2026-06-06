# Value-level ops over a tuple with a reference member: whole-tuple print and
# element print must show the referent (via the runtime's pointer-deref
# print_element overload), never the pointer address; hash() derefs likewise.
# Locks the access-as-value layer for the borrow (T*) tuple form.
from tpy import Int32, UInt64


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def __repr__(self) -> str:
        return f"Box({self.val})"
    def __hash__(self) -> UInt64:
        return UInt64(self.val)


def main() -> None:
    b = Box(5)
    t = (1, b)
    print(t)
    print(t[1])
    print(hash(t) == hash((1, Box(5))))
    b.val = 7
    print(t)


main()
