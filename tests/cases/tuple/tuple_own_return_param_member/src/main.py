# A param-rooted member returned as a per-element-Own tuple needs explicit
# copy() (the bound-name form now matches the literal form and the scalar
# Own[T] return). The Own slot receives a COPY -- the storage conversion must
# never MOVE through the borrow pointer and gut the caller's object: b reading
# 5 after the call is the regression lock (a destructive move would leave it
# moved-from/empty).
from tpy import Int32, Own, copy


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def f(b: Box) -> tuple[Own[Box], Int32]:
    pair = (copy(b), 0)   # tpyc: ok
    return pair


def main() -> None:
    b = Box(5)
    got, n = f(b)
    got.val = 99          # mutate the returned (copied) Box
    print(b.val)          # 5 -- the copy did not alias b
    print(got.val)        # 99
    print(n)              # 0


main()
