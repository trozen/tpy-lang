# @property with @native and @cpp_template on @native class methods
from tpy import Int32, readonly, pure
from tpy.extern import native, cpp_template

@native("std::vector")
class Vec[T]:
    @native("push_back")
    def add(self, value: T) -> None: ...

    # @property + @cpp_template getter
    @property
    @cpp_template("static_cast<int32_t>({self}.size())")
    @pure
    @readonly
    def count(self) -> Int32: ...

    # @property + @native rename getter
    @property
    @native("capacity")
    @pure
    @readonly
    def cap(self) -> Int32: ...

    # @property setter with @cpp_template
    @cap.setter
    @cpp_template("{self}.reserve({0})")
    def cap(self, value: Int32) -> None: ...

def main() -> None:
    v: Vec[Int32] = Vec[Int32]()
    v.add(10)
    v.add(20)
    v.add(30)

    # getter via @cpp_template
    n = v.count  # tpyc: type(Int32)
    print(n)
    print(v.count)

    # setter via @cpp_template
    v.cap = 100

    # getter via @native rename (deterministic after reserve)
    c = v.cap  # tpyc: type(Int32)
    print(c)

    print(v.count)

main()
