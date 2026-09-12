from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def take(a: Own[A] | None) -> int32:
    if a is None:
        return 0
    return a.n
def main() -> None:
    a = A(3)
    print(take(a))
    print(a.n)
main()
