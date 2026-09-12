from tpy import int32, readonly


class Base:
    @readonly
    def value(self) -> int32:
        return 7


class Child(Base):
    @readonly
    def value_plus_one(self) -> int32:
        return super().value() + 1  # tpyc: ok


print(Child().value_plus_one())
