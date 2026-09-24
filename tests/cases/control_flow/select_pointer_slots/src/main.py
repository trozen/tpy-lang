# A record / container select (`a if c else b`) meeting a pointer-repr
# `T | None` slot -- local, return, argument -- and a narrowed-Optional select
# as a method receiver: each aliases the chosen operand, never a copy.
from tpy import int32


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 100

    def area(self) -> int32:
        return self.n * 2


def pick(c: bool, a: C, b: C) -> C | None:
    return a if c else b  # tpyc: ok


def bump(x: C | None) -> None:
    if x is not None:
        x.n += 5


def local_records(c: bool) -> None:
    a = C(1)
    b = C(2)
    # Optional local from two record names: points at the chosen record.
    xs: C | None = a if c else b  # tpyc: ok
    if xs is not None:
        xs.n += 5
    print("local_records:", a.n, b.n)


def local_containers(c: bool) -> None:
    a: list[int32] = [1]
    b: list[int32] = [2, 3]
    # Optional local from two container names.
    xs: list[int32] | None = a if c else b  # tpyc: ok
    if xs is not None:
        xs.append(9)
    print("local_containers:", len(a), len(b))


def local_narrowed(c: bool, p: C | None, q: C | None) -> None:
    if p is not None and q is not None:
        # Optional local from two narrowed Optional names.
        xs: C | None = p if c else q  # tpyc: ok
        if xs is not None:
            xs.n += 5
        print("local_narrowed:", p.n, q.n)


def local_narrowed_plain(c: bool, p: C | None) -> None:
    b = C(2)
    if p is not None:
        # A narrowed Optional arm beside a plain record arm.
        xs: C | None = p if c else b  # tpyc: ok
        if xs is not None:
            xs.n += 5
        print("local_narrowed_plain:", p.n, b.n)


def local_none_arm(c: bool) -> None:
    a = C(1)
    # The None arm keeps the per-arm pointer render.
    xs: C | None = a if c else None  # tpyc: ok
    if xs is not None:
        xs.n += 5
    print("local_none_arm:", a.n, xs is None)


def return_select(c: bool) -> None:
    a = C(1)
    b = C(2)
    r = pick(c, a, b)
    if r is not None:
        r.n += 5
    print("return_select:", a.n, b.n)


def arg_select(c: bool) -> None:
    a = C(1)
    b = C(2)
    # An Optional argument from a select.
    bump(a if c else b)  # tpyc: ok
    # ... and from a select with a None arm.
    bump(a if c else None)  # tpyc: ok
    print("arg_select:", a.n, b.n)


def receiver_mutate(c: bool, p: C | None, q: C | None) -> None:
    if p is not None and q is not None:
        # A mutating method on a select of narrowed Optionals.
        (p if c else q).bump()  # tpyc: ok
        print("receiver_mutate:", p.n, q.n)


def receiver_read(c: bool, p: C | None, q: C | None) -> None:
    if p is not None and q is not None:
        # A reading method on the same select.
        print("receiver_read:", (p if c else q).area())  # tpyc: ok


def receiver_pointer_local(c: bool, a: C, b: C) -> None:
    x = a
    # `x` is rebound, so it is a pointer-local; the select still aliases.
    x = b
    (x if c else a).bump()  # tpyc: ok
    print("receiver_pointer_local:", a.n, b.n)


def main() -> None:
    for c in [True, False]:
        receiver_pointer_local(c, C(1), C(2))
        local_records(c)
        local_containers(c)
        local_narrowed(c, C(1), C(2))
        local_narrowed_plain(c, C(1))
        local_none_arm(c)
        return_select(c)
        arg_select(c)
        receiver_mutate(c, C(1), C(2))
        receiver_read(c, C(1), C(3))


main()
