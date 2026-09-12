# Constructor calls a free function defined before the class.
# Tests that function forward declarations are emitted before record definitions.
from tpy import int32

def twice(x: int32) -> int32:
    return x * 2

class Pair:
    a: int32
    b: int32

    def __init__(self, x: int32):
        self.a = x
        self.b = twice(x)

def main():
    p = Pair(5)
    print(p.a)
    print(p.b)

main()
