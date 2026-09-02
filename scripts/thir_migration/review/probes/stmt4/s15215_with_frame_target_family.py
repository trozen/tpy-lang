from tpy import Int32
class OCM:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def __enter__(self) -> Int32 | None:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

from typing import Iterator
def gen() -> Iterator[Int32]:
    with OCM(5) as x:
        pass
    yield 1
    if x is not None:
        yield x

def main() -> None:
    pass
main()
