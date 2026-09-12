from tpy import int32
class Base:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
class Sub(Base):
    def __init__(self, n: int32) -> None:
        Base.__init__(self, n)
class Holder:
    s: Sub
    def __init__(self, n: int32) -> None:
        self.s = Sub(n)
    def __neg__(self) -> Sub:
        return self.s
    def __invert__(self) -> Sub:
        return self.s
def pick(flag: bool) -> None:
    h = Holder(3)
    g = Holder(9)
    if flag:
        c: Base = -h
    else:
        c: Base = ~g
    print(c.n)
pick(True)
