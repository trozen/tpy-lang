# Test @native, @native(function=True), and @cpp_template on @native class methods
from tpy import Int32, Own, pure, readonly
from tpy.extern import native, cpp_template

@native("std::vector")
class Vec[T]:
    @native("push_back")
    def add(self, value: Own[T]) -> None: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::pop_back", function=True)
    def pop_last(self) -> T: ...

    @cpp_template("static_cast<int32_t>({self}.size())")
    @pure
    @readonly
    def count(self) -> Int32: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def get(self, index: Int32) -> T: ...

def main() -> None:
    v: Vec[Int32] = Vec[Int32]()
    v.add(10)
    v.add(20)
    v.add(30)
    print(v.count())
    print(v.get(1))
    print(v.pop_last())
    print(v.count())
    v.clear()
    print(v.count())

main()
