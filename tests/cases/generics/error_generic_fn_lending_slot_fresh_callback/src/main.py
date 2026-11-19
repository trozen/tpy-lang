# Error: a fresh callback at a generic `Fn[[T, T], T]` slot whose parameters
# borrow binds T to a borrow (its parameters) and a new value (its return);
# the hand-written twin refuses it too. `Fn[[T, T], Own[T]]` takes it.
from tpy import Fn, Own, copy, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def red[T](func: Fn[[T, T], T], a: list[T]) -> Own[T]:
    acc = copy(a[0])
    for i in range(1, len(a)):
        acc = func(acc, a[i])
    return acc


def combine(a: R, b: R) -> Own[R]:
    return R(a.n + b.n)


def main() -> None:
    rs = [R(1), R(2)]
    # The subject: combine builds a new R at a borrowing result slot.
    print(red(combine, rs).n)  # tpyc: error(/T is R from func's parameter but Own\[R\] from func's return; write Fn\[\[T, T\], Own\[T\]\] if func builds a new value/)


main()
