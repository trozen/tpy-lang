from tpy import Int32, error_return, ReturnException
class NotFound(Exception, ReturnException):
    pass
@error_return(NotFound)
def make(n: Int32) -> Int32:
    if n < 0:
        raise NotFound
    return n
def main() -> None:
    xs: list[Int32] = [0, 0]
    try:
        xs[0] = make(3)
        print(xs[0])
    except NotFound:
        print('e')
main()
