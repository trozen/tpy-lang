# Writing through BaseN.field in a @readonly method would mutate the
# ancestor subobject through a const receiver -- rejected, same rule as
# self.field mutation in a @readonly context.
from tpy import int32, readonly


class Parent:
    counter: int32


class Child(Parent):
    @readonly
    def bump(self) -> None:
        Parent.counter = Parent.counter + 1  # tpyc: error(/Cannot mutate readonly reference/)
