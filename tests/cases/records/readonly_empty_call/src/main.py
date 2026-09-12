# @readonly() with empty parens is equivalent to bare @readonly.
from tpy import readonly, int32

class Foo:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    @readonly()
    def get_x(self) -> int32:
        return self.x

def main():
    f = Foo(42)
    print(f.get_x())

main()
