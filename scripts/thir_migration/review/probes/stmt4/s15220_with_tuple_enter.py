from tpy import int32
class TCM:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __enter__(self) -> tuple[int32, int32]:
        return (self.n, self.n)
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass
def f(n: int32) -> int32:
    with TCM(n) as t:
        return t[0]
def main() -> None:
    print(f(1))
main()
