# Test: super().__init__() when parent has no explicit __init__ (calls default constructor)

class Base:
    pass


class Child(Base):
    value: int

    def __init__(self, value: int) -> None:
        super().__init__()  # Valid: calls implicit default constructor
        self.value = value


c = Child(42)
print(c.value)
