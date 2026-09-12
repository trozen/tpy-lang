# D16 Phase 1: undeclared attr access on a class without __getattr__ stays an error.
from tpy import int32

class Plain:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

def main() -> None:
    p = Plain(int32(1))
    print(p.unknown)  # tpyc: error(/no field 'unknown'/)

main()
