from tpy import int32
class OCM:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __enter__(self) -> int32 | None:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

from typing import Iterator
def gen() -> Iterator[int32]:
    with OCM(5) as x:
        pass
    yield 1
    if x is not None:
        yield x

def main() -> None:
    pass
main()
