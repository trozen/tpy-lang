from tpy import Int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def items(n: Int32) -> Own[list[Int32]]:
    if n < 0:
        raise Err
    return [n]
@error_return(Err)
def caller(n: Int32) -> Int32:
    xs = items(n)
    print(len(xs))
    return n
def main() -> None:
    try:
        print(caller(1))
    except Err:
        print("err")
main()
