# A nested Own in an inferred subclass field is rejected as redundant, same as
# for a base-less class (the inference only unwraps a top-level Own).
from tpy import Own, readonly, nocopy

@nocopy
class Res:
    def __init__(self, n: int):
        self.n = n

class Base:
    pass

class Sub(Base):
    def __init__(self, r: readonly[Own[Res]]):
        self.r = r  # tpyc: error(/Own\[T\] is redundant in this field type/)

def main() -> None:
    pass

main()
