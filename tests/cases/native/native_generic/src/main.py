# Generic @native: C++ template deduction (no explicit type args emitted)
from tpy.extern import native, cpp_template
from tpy import int32, Ptr, uint32, take_ptr

@native("tpy::__len__")
def my_len[T](x: T) -> int32: ...

@cpp_template("{0}[{1}]")
def load[T](p: Ptr[T], offset: uint32) -> T: ...

def main() -> None:
    items: list[int32] = [10, 20, 30]
    print(my_len(items))

    p = take_ptr(items[0])
    print(load(p, uint32(2)))

main()
