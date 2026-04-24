# The first argument to BaseN.method(...) must be literally 'self'. Passing
# any other expression (another parameter, a local, a field) is rejected.
from tpy import Ptr


class Parent:
    def foo(self) -> int:
        return 1


class Child(Parent):
    def use(self, other: Ptr[Parent]) -> int:
        return Parent.foo(other)  # tpyc: error(/must pass 'self' as the first argument/)
