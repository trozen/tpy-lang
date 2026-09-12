# Free @native function with cpp_return_type: declared TPy return narrows
# the C++ side's wider return (size_t -> int32_t). Codegen wraps the call in
# static_cast<int32_t>(...) so the implicit narrowing is explicit (no
# -Wsign-conversion / -Wconversion at the use site).
from tpy import int32, uint64
from tpy.extern import native

@native("nx::wide_count", cpp_return_type=uint64)
def wide_count() -> int32: ...

def main() -> None:
    n = wide_count()  # tpyc: type(int32)
    print(n)

main()
