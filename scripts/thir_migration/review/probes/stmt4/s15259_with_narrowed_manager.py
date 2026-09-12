from tpy import int32
class CM:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __enter__(self) -> int32:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.n)

def f(c: CM | None) -> int32:
    if c is None:
        return 0
    with c as v:
        return v
def main() -> None:
    print(f(CM(1)))
main()
