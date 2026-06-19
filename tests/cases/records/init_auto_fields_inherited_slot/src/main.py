# A subclass assigns a name that is already a PARENT field: it must reuse the
# single inherited slot, not declare a shadow. A base method reads the slot and
# must see the child-assigned value (a shadow would read the uninit parent slot).
from tpy import Int32

class Base:
    x: Int32
    def __init__(self, x: Int32):
        self.x = x
    def get_x(self) -> Int32:
        return self.x

class Child(Base):
    def __init__(self, x: Int32):
        self.x = x          # x is Base.x -- reuse the inherited slot, no shadow

def main() -> None:
    c = Child(7)
    print(c.get_x())        # base method sees the child-assigned value -> 7

main()
