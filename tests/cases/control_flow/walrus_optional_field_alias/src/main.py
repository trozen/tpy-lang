# Walrus binding from a storage-form Optional field lifts via
# optional_to_ptr into the pointer-form target. The binding ALIASES the field
# (mutation through it is visible on the source), matching CPython; pre-fix
# this was a hard g++ error (std::optional<Box> -> Box* assignment).
class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


class Holder:
    opt: Box | None
    def __init__(self, b: Box | None) -> None:
        self.opt = b


def main() -> None:
    h = Holder(Box(1))
    if (t := h.opt) is not None:
        t.val = 99               # writes through the alias
    if h.opt is not None:
        print(h.opt.val)         # 99 -- mutation visible on the field

    empty = Holder(None)
    if (u := empty.opt) is not None:
        print("unexpected", u.val)
    else:
        print("none ok")


main()
