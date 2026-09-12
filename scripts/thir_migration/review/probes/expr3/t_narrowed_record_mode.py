from tpy import int32
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
    def __bool__(self) -> bool:
        return self.n > 0
class B:
    m: int32
    def __init__(self, m: int32) -> None:
        self.m = m
def show(x: A | B) -> None:
    if isinstance(x, A):
        if x:
            print("A true")
        else:
            print("A false")
    else:
        print("B")
def main() -> None:
    show(A(1))
    show(B(2))
main()
