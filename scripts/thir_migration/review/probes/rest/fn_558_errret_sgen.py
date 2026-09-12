from tpy import int32
from typing import Iterator
from tpy import error_return, ReturnException
class E(Exception, ReturnException):
    pass
@error_return(E)
def g(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i
def main() -> None:
    try:
        for v in g(3):
            print(v)
    except E:
        print("e")
main()
