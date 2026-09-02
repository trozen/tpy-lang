from tpy import Int32
class CM:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def __enter__(self) -> Int32:
        return self.n
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.n)

def f(n: Int32) -> Int32:
    if n > 0:
        with CM(n) as c:
            v = c + 1
        print(v)
    return n
def main() -> None:
    print(f(1))
main()
