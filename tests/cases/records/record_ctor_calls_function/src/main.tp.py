# Constructor calls a free function defined before the class.
# Tests that function forward declarations are emitted before record definitions.
from tpy import Int32

def twice(x: Int32) -> Int32:
    return x * 2

class Pair:
    a: Int32
    b: Int32

    def __init__(self, x: Int32):
        self.a = x
        self.b = twice(x)

def main():
    p = Pair(5)
    print(p.a)
    print(p.b)

main()
