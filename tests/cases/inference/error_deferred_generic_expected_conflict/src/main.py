# Deferred generic inference: partial inference conflicts with expected type
from tpy import Int32, Int64

class Pair[T, U]:
    a: T
    b: U
    def __init__(self) -> None:
        pass
    def set_a(self, val: T) -> None:
        self.a = val

def consume(p: Pair[Int64, Int32]) -> None:
    pass

def main() -> None:
    p = Pair()
    p.set_a(Int32(1))  # T = Int32, U still pending
    consume(p)  # tpyc: error(/Conflicting type for 'T'.*previously inferred as 'Int32'.*requires 'Int64'/)

main()
