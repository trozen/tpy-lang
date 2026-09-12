# Error: callable class return type doesn't match Fn hint
from tpy import int32, Fn

class ReturnsInt:
    def __call__(self, x: int32) -> int32:
        return x

def apply(f: Fn[[int32], str], x: int32) -> str:
    return f(x)

def main():
    r = ReturnsInt()
    apply(r, 5)  # tpyc: error(/no '__call__' overload .*matches/)

main()
