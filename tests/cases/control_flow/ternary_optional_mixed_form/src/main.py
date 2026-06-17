# A ternary joining a borrow-form arm (pointer-repr Optional param) and a
# storage-form arm (Optional field -> std::optional<T>) normalizes each arm to
# T* (optional_to_ptr on the field arm), so the C++ ?: operands match. The
# binding ALIASES the chosen arm; mutation through it is visible on the source,
# matching CPython. Pre-fix this emitted mixed ?: operands (Box* vs optional).
class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


class Holder:
    opt: Box | None
    def __init__(self, b: Box | None) -> None:
        self.opt = b


def bump(p: Box | None, h: Holder, c: bool) -> None:
    t = p if c else h.opt
    if t is not None:
        t.val += 100             # writes through whichever arm aliased


def main() -> None:
    h = Holder(Box(7))
    param = Box(3)
    bump(param, h, True)         # param arm
    print(param.val)             # 103 -- visible on caller's object
    bump(param, h, False)        # field arm
    if h.opt is not None:
        print(h.opt.val)         # 107 -- visible on the field


main()
