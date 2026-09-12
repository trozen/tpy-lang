# @cpp_template: inline C++ expression templates in .py source
from tpy.extern import cpp_template
from tpy import int32, char

@cpp_template("static_cast<char>({0})")
def to_char(i: int32) -> char: ...

@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def to_int(c: char) -> int32: ...

@cpp_template("{0} + {1}")
def add(a: int32, b: int32) -> int32: ...

# A repeated *type* placeholder ({T}) is inert -- resolved before argument
# expansion -- so it stays legal even though a repeated *value* placeholder
# (like {0}) would be rejected.
@cpp_template("static_cast<{T}>(static_cast<{T}>({0}))")
def round_trip[T](x: int32) -> int32: ...

def main() -> None:
    print(to_char(65))
    print(to_int("Z"))
    print(add(10, 32))
    print(round_trip[int32](7))

main()
