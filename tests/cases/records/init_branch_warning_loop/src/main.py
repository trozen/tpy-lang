# Field initialized in init section, then accumulated in loop body -- no warning.
from tpy import int32

class Accum:
    total: int32
    def __init__(self, n: int32):
        self.total = int32(0)
        i: int32 = int32(0)
        while i < n:
            self.total = self.total + i  # tpyc: ok
            i = i + int32(1)

def main() -> None:
    a = Accum(int32(4))
    print(a.total)

main()
