# @property with @native and @cpp_template on @native class methods
from tpy import int32, uint64, readonly, pure
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
    def count(self) -> int32: ...

    # @property + @native rename, with cpp_return_type declaring that the
    # underlying C++ method (`capacity()`) returns size_t. Codegen wraps the
    # call in static_cast<int32_t>(...) so -Wsign-conversion / -Wconversion
    # don't fire. Without cpp_return_type, @native means exact-match-to-C++
    # and the implicit narrowing would warn.
    @property
    @native("capacity", cpp_return_type=uint64)
    @pure
    @readonly
    def cap(self) -> int32: ...

    # @property setter with @cpp_template
    @cap.setter
    @cpp_template("{self}.reserve({0})")
    def cap(self, value: int32) -> None: ...

def main() -> None:
    v: Vec[int32] = Vec[int32]()
    v.add(10)
    v.add(20)
    v.add(30)

    # getter via @cpp_template
    n = v.count  # tpyc: type(int32)
    print(n)
    print(v.count)

    # setter via @cpp_template
    v.cap = 100

    # getter via @native(cpp_return_type=uint64) (deterministic after reserve)
    c = v.cap  # tpyc: type(int32)
    print(c)

    print(v.count)

main()
