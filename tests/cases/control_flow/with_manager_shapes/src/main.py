# with-statement shapes over scalar-arg/no-arg managers: multi-manager LIFO
# exit, bool-__exit__ suppression, record-returning __enter__ (the borrow
# observed via mutation), a pointer-local manager, and break/continue/return
# through the with body.


class Gate:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __enter__(self) -> int:
        print("enter", self.n)
        return self.n

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("exit", self.n)


class Sup:
    def __enter__(self) -> int:
        return 0

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print("suppressed:", str(exc_val))
            return True
        return False


class Guard:
    depth: int

    def __init__(self) -> None:
        self.depth = 0

    def __enter__(self) -> "Guard":
        self.depth += 1
        return self

    def __exit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("guard exit at", self.depth)


class Holder:
    g: Guard

    def __init__(self) -> None:
        self.g = Guard()


def boom() -> None:
    raise ValueError("boom")


def multi() -> None:
    with Gate(1), Gate(2):
        print("body")


def suppress() -> None:
    with Sup():
        print("before")
        boom()
    print("after suppressed")


def ref_target() -> None:
    # The as-binding borrows the manager (auto&): mutation through it is
    # visible on the shared object after the block.
    with Guard() as g:
        g.depth += 10
        print("inside:", g.depth)
    print("after:", g.depth)


def deref_manager(h: Holder, h2: Holder) -> None:
    # A reassigned borrow local is a T* pointer-local; using it as the
    # manager must borrow the pointee (deref), not copy the pointer.
    m = h.g
    with m:
        print("in:", m.depth)
    print("mid:", m.depth)
    m = h2.g
    with m:
        print("in2:", m.depth)
    print("post:", m.depth)


def loop_exits(cm: Gate) -> None:
    for i in range(4):
        with cm:
            if i == 1:
                continue
            if i == 3:
                break
            print("i =", i)
    print("loop done")


def ret_through(cm: Gate) -> int:
    with cm:
        return 42


def main() -> None:
    multi()
    print("---")
    suppress()
    print("---")
    ref_target()
    print("---")
    deref_manager(Holder(), Holder())
    print("---")
    loop_exits(Gate(7))
    print("---")
    print(ret_through(Gate(9)))


main()
