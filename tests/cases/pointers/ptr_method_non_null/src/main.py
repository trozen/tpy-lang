# A second method call through the same Ptr receiver is proven non-null by
# the first (post-access narrowing): deref_check on the first call, `->` on
# the second -- pins both pointer-receiver method-call renders.
from tpy import Int32, Ptr, take_ptr

class Cell:
    n: Int32

    def __init__(self):
        self.n = 3

    def val(self) -> Int32:
        return self.n

    def bump(self, k: Int32) -> Int32:
        return self.n + k

def read_twice(p: Ptr[Cell]) -> Int32:
    a = p.val()    # tpyc: nullable(p)
    b = p.bump(2)  # tpyc: non_null(p)
    return a + b

def main():
    c = Cell()
    print(read_twice(take_ptr(c)))

main()
