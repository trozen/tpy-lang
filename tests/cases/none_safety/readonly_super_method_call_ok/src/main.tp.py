from tpy import Int32, readonly


class Base:
    @readonly
    def value(self) -> Int32:
        return 7


class Child(Base):
    @readonly
    def value_plus_one(self) -> Int32:
        return super().value() + 1  # tpyc: ok


print(Child().value_plus_one())
