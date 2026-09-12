from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
from typing import overload
from tpy import int32
@overload
def f(a: int32) -> int32: ...
@overload
def f(a: int32, b: int32) -> int32: ...
@error_return(E)
def f(a: int32, b: int32 = 0) -> int32:
    return a + b
def main() -> None:
    try:
        print(f(1), f(1, 2))
    except E:
        print("e")
main()
