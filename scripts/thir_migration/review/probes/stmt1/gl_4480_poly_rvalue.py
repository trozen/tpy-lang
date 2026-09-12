from tpy import int32
class Base:
    n: int32
    def __init__(self) -> None:
        self.n = 1
class Child(Base):
    def __init__(self) -> None:
        self.n = 2
b: Base = Child()
print(b.n)
