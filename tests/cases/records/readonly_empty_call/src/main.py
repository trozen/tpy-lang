# @readonly() with empty parens is equivalent to bare @readonly.
from tpy import readonly, Int32

class Foo:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    @readonly()
    def get_x(self) -> Int32:
        return self.x

def main():
    f = Foo(42)
    print(f.get_x())

main()
