# BaseN.__init__(self, ...) is only callable inside the child's __init__.
# Calling it from any other method would run the base constructor after the
# object is already fully constructed, which isn't a meaningful operation and
# doesn't map to valid C++.
from tpy import Int32


class Parent:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class Child(Parent):
    def __init__(self) -> None:
        Parent.__init__(self, Int32(1))

    def reset(self, a: Int32) -> None:
        Parent.__init__(self, a)  # tpyc: error(/can only be called inside '__init__'/)
