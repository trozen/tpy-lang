from tpy import int32
class CM:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __enter__(self) -> int32:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.n)

def f(n: int32) -> int32:
    if n > 0:
        with CM(n) as c:
            v = c + 1
        print(v)
    return n
def main() -> None:
    print(f(1))
main()
