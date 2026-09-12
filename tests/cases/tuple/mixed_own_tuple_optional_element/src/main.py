# A mixed owned+borrow tuple whose BORROWED element is nullable
# (`tuple[Own[Box], Box | None]`) renders that element as a bare `Box*`, so
# reading it must not lift it a second time through `optional_to_ptr`. The
# read is narrowed and then written through, and the caller reads the original
# afterwards -- a copy anywhere on that path shows up as a wrong number.
from tpy import int32, Own


class Box:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


def make_mixed(b: Box) -> tuple[Own[Box], Box | None]:
    return (Box(1), b)


def make_none() -> tuple[Own[Box], Box | None]:
    return (Box(2), None)


def write_through(b: Box) -> int32:
    p = make_mixed(b)
    e = p[1]
    if e is not None:
        e.val = 42
    return p[0].val


def read_direct(b: Box) -> int32:
    # No intermediate local. Narrowing does not track a subscript path, so the
    # read keeps its runtime null check -- unrelated to the element's form.
    p = make_mixed(b)
    if p[1] is not None:
        return p[1].val  # tpyc: warning(/Potential None access/)
    return -1


def none_element() -> int32:
    p = make_none()
    e = p[1]
    if e is None:
        return p[0].val
    return -1


def main() -> None:
    b = Box(7)
    print("write:", write_through(b), b.val)
    print("read:", read_direct(b))
    print("none:", none_element())


main()
