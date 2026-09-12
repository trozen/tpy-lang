from tpy import int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def small(n: int32) -> int32:
    if n < 0:
        raise Err
    return n
def use(n: int32) -> int:
    try:
        v: int = small(n)
    except Err:
        return -1
    return v
print(use(2))
