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
    with CM(n) as c:
        xs = [c]
    xs = [n, n]
    return len(xs)
def main() -> None:
    print(f(1))
main()
