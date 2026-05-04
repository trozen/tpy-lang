# D16 Phase 1: undeclared attr access on a class without __getattr__ stays an error.
from tpy import Int32

class Plain:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

def main() -> None:
    p = Plain(Int32(1))
    print(p.unknown)  # tpyc: error(/no field 'unknown'/)

main()
