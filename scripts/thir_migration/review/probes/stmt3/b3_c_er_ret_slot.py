from tpy import int32, error_return, ReturnException
class NotFound(Exception, ReturnException):
    pass
@error_return(NotFound)
def inner(n: int32) -> int32:
    if n < 0:
        raise NotFound
    return n
@error_return(NotFound)
def outer(n: int32) -> int32 | None:
    return inner(n)
def main() -> None:
    try:
        r = outer(1)
        print(1 if r is None else 0)
    except NotFound:
        print('e')
main()
