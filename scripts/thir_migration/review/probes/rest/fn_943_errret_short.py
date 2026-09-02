from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
from typing import overload
from tpy import Int32
@overload
def f(a: Int32) -> Int32: ...
@overload
def f(a: Int32, b: Int32) -> Int32: ...
@error_return(E)
def f(a: Int32, b: Int32 = 0) -> Int32:
    return a + b
def main() -> None:
    try:
        print(f(1), f(1, 2))
    except E:
        print("e")
main()
