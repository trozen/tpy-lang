# A rebound tuple that owns an element, bound to an @error_return call's
# result: that unwrap does not place the elements one by one, so it is
# refused (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from tpy import Own, ReturnException, error_return, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Fail(Exception, ReturnException):
    pass


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


@error_return(Fail)
def mk(n: int32, b: Box) -> tuple[Own[Box], Box]:
    if n < 0:
        raise Fail()
    return (Box(n), b)


@error_return(Fail)
def f(v: Box) -> int32:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    r = p[0].n
    # The direct @error_return binding of the same name.
    p = mk(3, v)  # tpyc: error(/binding 'p' to the result of an @error_return call is not yet supported here: .p. keeps objects of its own element by element, and this binding does not place them one by one/)
    return r * 100 + p[0].n


try:
    print(f(Box(2)))
except Fail:
    print("fail")
