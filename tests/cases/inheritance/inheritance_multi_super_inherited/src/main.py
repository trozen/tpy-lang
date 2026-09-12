# `foo` is defined on A and inherited by B; super().foo() skips past B in D's
# MRO and resolves to A directly. `bar` is owned by C, so super().bar() picks C.
from tpy import int32


class A:
    def foo(self) -> str:
        return "A.foo"


class B(A):
    pass


class C:
    def bar(self) -> str:
        return "C.bar"


class D(B, C):
    def describe(self) -> str:
        return super().foo() + " + " + super().bar()


def main() -> None:
    d = D()
    print(d.describe())


main()
