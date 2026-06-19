# Subclass variant: an inferred field from an `Own[T]` param on a class with a
# base also unwraps to a `T` field (the sema-path inference), moving the @nocopy in.
from tpy import Own, nocopy

@nocopy
class Res:
    def __init__(self, n: int):
        self.n = n

class Base:
    pass

class Holder(Base):
    def __init__(self, r: Own[Res]):
        self.r = r
    def value(self) -> int:
        return self.r.n

def main() -> None:
    print(Holder(Res(9)).value())

main()
