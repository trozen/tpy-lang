# Field initialized in init section, then accumulated in loop body -- no warning.
from tpy import Int32

class Accum:
    total: Int32
    def __init__(self, n: Int32):
        self.total = Int32(0)
        i: Int32 = Int32(0)
        while i < n:
            self.total = self.total + i  # tpyc: ok
            i = i + Int32(1)

def main() -> None:
    a = Accum(Int32(4))
    print(a.total)

main()
