from typing import Optional
from tpy import Int32
class Base:
    n: Int32
    def __init__(self) -> None:
        self.n = 1
    def tag(self) -> Int32:
        return self.n
class Child(Base):
    def __init__(self) -> None:
        self.n = 2
def probe() -> Int32:
    b: Optional[Base] = Child()
    if b is None:
        return -1
    return b.tag()
def main() -> None:
    print(probe())
main()
