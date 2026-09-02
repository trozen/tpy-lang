from tpy import Int32
class Base:
    n: Int32
    def __init__(self) -> None:
        self.n = 1
class Child(Base):
    def __init__(self) -> None:
        self.n = 2
def main() -> None:
    other = Base()
    b: Base = Child()
    print(b.n)
    b = other
    print(b.n)
main()
