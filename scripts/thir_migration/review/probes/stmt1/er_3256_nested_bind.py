from tpy import Int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def g(n: Int32) -> Int32:
    if n < 0:
        raise Err
    return n
@error_return(Err)
def f(n: Int32) -> Int32:
    return g(n) * 2
@error_return(Err)
def h(n: Int32) -> Int32:
    v = f(g(n))
    return v
def main() -> None:
    try:
        print(h(3))
    except Err:
        print("err")
main()
