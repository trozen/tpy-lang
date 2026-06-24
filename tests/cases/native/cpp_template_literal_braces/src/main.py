# @cpp_template free functions with literal C++ braces, spelled via the
# {{ }} escape (a lambda body and a GCC statement-expression).
from tpy.extern import cpp_template
from tpy import Int32

@cpp_template("[]() {{ return {0} + {1}; }}()")  # tpyc: ok
def lambda_sum(a: Int32, b: Int32) -> Int32: ...

@cpp_template("({{ int t = {0}; t * t; }})")  # tpyc: ok
def square(x: Int32) -> Int32: ...

def main() -> None:
    print(lambda_sum(20, 22))
    print(square(7))

main()
