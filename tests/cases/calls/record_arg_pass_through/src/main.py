# Record names as free-fn call args (const/mutated slots, reseated borrow,
# narrowed alias); callee mutations observed on the caller's object (aliasing).
from tpy import Int32


class A:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


class B:
    y: Int32

    def __init__(self, y: Int32):
        self.y = y


class Holder:
    a: A
    b: A

    def __init__(self):
        self.a = A(1)
        self.b = A(2)


def read_rec(a: A) -> Int32:
    return a.x


def mutate_rec(a: A) -> None:
    a.x += 10


def pass_both(a: A, b: A) -> Int32:
    mutate_rec(b)
    return read_rec(a) + read_rec(b)


def through_pointer(h: Holder, flag: bool) -> Int32:
    p = h.a
    if flag:
        p = h.b
    mutate_rec(p)
    return read_rec(p)


def through_narrowing(v: A | B) -> Int32:
    if isinstance(v, A):
        mutate_rec(v)
        return read_rec(v)
    return v.y


def main():
    h = Holder()
    print(pass_both(h.a, h.b))  # mutates h.b through the callee
    print(h.b.x)                # 12: the caller's object changed
    print(through_pointer(h, True))
    print(h.b.x)                # 22: mutated through the reseated borrow
    print(through_pointer(h, False))
    print(h.a.x)                # 11: the other pointee
    a = A(5)
    print(through_narrowing(a))
    print(a.x)                  # 15: mutated through the narrowed alias
    print(through_narrowing(B(7)))


main()
