from tpy import Int32
class R:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
class Base(Exception):
    r: R
    def __init__(self, r: R) -> None:
        super().__init__('base')
        self.r = r
class Derived(Base):
    pass
def f(n: Int32) -> Int32:
    if n > 0:
        raise Derived(R(n))
    return n
def main() -> None:
    print(f(0))
main()
