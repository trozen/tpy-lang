# @cpp_template: inline C++ expression templates in .py source
from tpy.extern import cpp_template
from tpy import Int32, Char

@cpp_template("static_cast<char>({0})")
def to_char(i: Int32) -> Char: ...

@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def to_int(c: Char) -> Int32: ...

@cpp_template("{0} + {1}")
def add(a: Int32, b: Int32) -> Int32: ...

def main() -> None:
    print(to_char(65))
    print(to_int("Z"))
    print(add(10, 32))

main()
