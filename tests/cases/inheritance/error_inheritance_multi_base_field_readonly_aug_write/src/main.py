# Augmented assignment through BaseN.field in a @readonly method is
# rejected on the same grounds as direct assignment: implicit receiver
# is a const `this`.
from tpy import int32, readonly


class Parent:
    counter: int32


class Child(Parent):
    @readonly
    def bump_aug(self) -> None:
        Parent.counter += 1  # tpyc: error(/Cannot mutate readonly reference/)
