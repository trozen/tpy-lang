# Multi-base super().method() must resolve methods that the matching parent
# inherits (not just its own methods). Here `foo` lives on A; B inherits it
# without overriding; D(B, C) calls super().foo() and must pick B's chain.
from tpy import Int32


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
