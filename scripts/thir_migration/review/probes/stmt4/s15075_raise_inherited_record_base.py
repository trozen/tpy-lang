from tpy import int32
class R:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Base(Exception):
    r: R
    def __init__(self, r: R) -> None:
        super().__init__('base')
        self.r = r
class Derived(Base):
    pass
def f(n: int32) -> int32:
    if n > 0:
        raise Derived(R(n))
    return n
def main() -> None:
    print(f(0))
main()
