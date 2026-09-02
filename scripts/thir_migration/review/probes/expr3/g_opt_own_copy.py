from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def take(a: Own[A] | None) -> Int32:
    if a is None:
        return 0
    return a.n
def main() -> None:
    a = A(3)
    print(take(a))
    print(a.n)
main()
