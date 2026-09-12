from tpy import int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def g(n: int32) -> int32:
    if n < 0:
        raise Err
    return n
@error_return(Err)
def f(n: int32) -> int32:
    return g(n) * 2
@error_return(Err)
def h(n: int32) -> int32:
    v = f(g(n))
    return v
def main() -> None:
    try:
        print(h(3))
    except Err:
        print("err")
main()
