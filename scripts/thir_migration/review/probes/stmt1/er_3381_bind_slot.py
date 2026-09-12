from tpy import int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def items(n: int32) -> Own[list[int32]]:
    if n < 0:
        raise Err
    return [n]
@error_return(Err)
def caller(n: int32) -> int32:
    xs = items(n)
    print(len(xs))
    return n
def main() -> None:
    try:
        print(caller(1))
    except Err:
        print("err")
main()
