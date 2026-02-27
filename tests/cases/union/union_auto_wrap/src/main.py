# Test passing concrete types to functions expecting union parameters
class A:
    x: int
    def __init__(self, x: int) -> None:
        self.x = x

class B:
    y: str
    def __init__(self, y: str) -> None:
        self.y = y

def process(v: A | B) -> None:
    if isinstance(v, A):
        print(v.x)
    else:
        print(v.y)

def main() -> None:
    a: A = A(42)
    b: B = B("hello")
    process(a)
    process(b)

main()
