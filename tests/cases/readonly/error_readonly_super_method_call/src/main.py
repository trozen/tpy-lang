from tpy import int32, readonly


class Base:
    def value(self) -> int32:
        return 7


class Child(Base):
    @readonly
    def value_plus_one(self) -> int32:
        return super().value() + 1  # tpyc: error(/Cannot call non-readonly method 'value' on readonly reference/)


print(0)
