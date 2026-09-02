from tpy import Int32
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
    def __bool__(self) -> bool:
        return self.n > 0
class B:
    m: Int32
    def __init__(self, m: Int32) -> None:
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
