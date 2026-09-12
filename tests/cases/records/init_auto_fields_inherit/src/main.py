# A subclass declares fields via `self.f = param` in __init__ (no class-level
# annotation): `x` is inherited from Base (reuses the parent slot), `y` is new.
from tpy import int32

class Base:
    x: int32
    def __init__(self, x: int32):
        self.x = x

class Child(Base):
    def __init__(self, x: int32, y: int32):
        super().__init__(x)
        self.y = y          # new own field, inferred from the param

def main() -> None:
    c = Child(3, 4)
    print(c.x)
    print(c.y)
    c.y += 10               # mutate the inferred field
    print(c.y)

main()
