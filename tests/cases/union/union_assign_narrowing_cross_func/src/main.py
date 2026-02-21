# Assignment narrowing in one function must not leak into another
class A:
    x: float
    def __init__(self, x: float) -> None:
        self.x = x

class B:
    y: float
    def __init__(self, y: float) -> None:
        self.y = y

class C:
    x: float
    def __init__(self, x: float) -> None:
        self.x = x

def f() -> None:
    a: A | B = A(1.0)
    print(a.x)

def g(a: C) -> None:
    print(a.x)

def main() -> None:
    f()
    g(C(2.0))

main()
