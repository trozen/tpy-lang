# Deferred generic inference: partial inference conflicts with expected type
from tpy import int32, int64

class Pair[T, U]:
    a: T
    b: U
    def __init__(self) -> None:
        pass
    def set_a(self, val: T) -> None:
        self.a = val

def consume(p: Pair[int64, int32]) -> None:
    pass

def main() -> None:
    p = Pair()
    p.set_a(int32(1))  # T = int32, U still pending
    consume(p)  # tpyc: error(/Conflicting type for 'T'.*previously inferred as 'int32'.*requires 'int64'/)

main()
