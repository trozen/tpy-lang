from tpy import Int32
class TCM:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def __enter__(self) -> tuple[Int32, Int32]:
        return (self.n, self.n)
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
def f(n: Int32) -> Int32:
    with TCM(n) as t:
        return t[0]
def main() -> None:
    print(f(1))
main()
