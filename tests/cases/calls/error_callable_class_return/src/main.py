# Error: callable class return type doesn't match Fn hint
from tpy import Int32, Fn

class ReturnsInt:
    def __call__(self, x: Int32) -> Int32:
        return x

def apply(f: Fn[[Int32], str], x: Int32) -> str:
    return f(x)

def main():
    r = ReturnsInt()
    apply(r, 5)  # tpyc: error(/no '__call__' overload .*matches/)

main()
