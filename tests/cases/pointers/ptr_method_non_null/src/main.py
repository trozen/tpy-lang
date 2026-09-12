# A second method call through the same Ptr receiver is proven non-null by
# the first (post-access narrowing): deref_check on the first call, `->` on
# the second -- pins both pointer-receiver method-call renders.
from tpy import int32, Ptr, take_ptr

class Cell:
    n: int32

    def __init__(self):
        self.n = 3

    def val(self) -> int32:
        return self.n

    def bump(self, k: int32) -> int32:
        return self.n + k

def read_twice(p: Ptr[Cell]) -> int32:
    a = p.val()    # tpyc: nullable(p)
    b = p.bump(2)  # tpyc: non_null(p)
    return a + b

def main():
    c = Cell()
    print(read_twice(take_ptr(c)))

main()
