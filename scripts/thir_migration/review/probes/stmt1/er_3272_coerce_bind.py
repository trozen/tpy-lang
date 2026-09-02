from tpy import Int32, Own, error_return, ReturnException
class Err(Exception, ReturnException):
    pass
@error_return(Err)
def small(n: Int32) -> Int32:
    if n < 0:
        raise Err
    return n
def use(n: Int32) -> int:
    try:
        v: int = small(n)
    except Err:
        return -1
    return v
print(use(2))
