# Value-union method arguments: a member-valued scalar hoists a variant temp
# at the call, while a coerced literal passes bare.
from tpy import Float64, Int32


class A:
    x: Int32
    u: Int32 | Float64

    def __init__(self, x: Int32) -> None:
        self.x = x
        self.u = 0

    # Discriminates on Float64 (== float under CPython) so a plain-int
    # argument narrows the same way on both runtimes.
    def tag(self, v: Int32 | Float64) -> Int32:
        if isinstance(v, Float64):
            return self.x
        return self.x + v

    def helper(self) -> Int32:
        return self.x + 1

    def outer(self) -> Int32:
        # The self receiver renders through the arrow.
        return self.helper()


class Child(A):
    def __init__(self, x: Int32) -> None:
        super().__init__(x)


def use(a: A, k: Int32, f: Float64) -> Int32:
    # Each scalar argument hoists its own variant temp.
    r = a.tag(k)
    r = a.tag(f)
    return r


def use_inherited(c: Child, k: Int32) -> Int32:
    return c.tag(k)


def use_lit(a: A) -> Int32:
    # A coerced literal needs no temp.
    return a.tag(5)


def main() -> None:
    a = A(10)
    print(use(a, 2, 0.5))
    print(use_inherited(Child(3), 4))
    print(use_lit(a))
    print(a.outer())


main()
