# Field assigned inside while/for loops in __init__ produces a warning.
from tpy import Int32

class Accum:
    total: Int32
    def __init__(self, n: Int32):
        self.total = Int32(0)
        i: Int32 = Int32(0)
        while i < n:
            self.total = self.total + i  # tpyc: warning(/bypasses the C\+\+ member initializer list/)
            i = i + Int32(1)

def main() -> None:
    a = Accum(Int32(4))
    print(a.total)

main()
