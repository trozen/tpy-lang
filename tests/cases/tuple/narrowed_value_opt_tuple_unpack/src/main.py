# A NARROWED value-repr `Optional[value tuple]` name (`r: tuple[float, int32] | None`
# after `if r is not None`) unpacks through the deref read; the value-opt tuple RETURN slot.
from tpy import int32, error_return, ReturnException


# return idiom: a tuple literal / `None` into the value-opt tuple return slot
def find(k: int32) -> tuple[float, int32] | None:
    if k > 0:
        return (1.5, k)  # tpyc: ok
    return None  # tpyc: ok


# return idiom: an un-narrowed value-tuple NAME into the same slot
def find_name(k: int32) -> tuple[float, int32] | None:
    t = (2.5, k)
    return t  # tpyc: ok


# return idiom: a str element
def find_str(k: int32) -> tuple[str, int32] | None:
    if k > 0:
        return ("x", k)  # tpyc: ok
    return None


# return idiom: a str-element tuple NAME into the slot
def find_str_name(k: int32) -> tuple[str, int32] | None:
    t = ("y", k)
    return t  # tpyc: ok


# free-function param: the reproducer
def use(r: tuple[float, int32] | None) -> None:
    if r is not None:
        a, b = r  # tpyc: ok
        print("param", a, b)
    else:
        print("param none")


class K:
    tup: tuple[float, int32] | None

    def __init__(self, tup: tuple[float, int32] | None) -> None:
        self.tup = tup

    # method: the same param unpack inside a method body
    def use(self, r: tuple[float, int32] | None) -> None:
        if r is not None:
            a, b = r  # tpyc: ok
            print("method", a, b)

    # inverse: a FIELD source keeps the field arm's `(*this->tup)` render
    def show(self) -> None:
        if self.tup is not None:
            a, b = self.tup  # tpyc: ok
            print("field", a, b)
        else:
            print("field none")


# try/finally body
def use_finally(r: tuple[float, int32] | None) -> None:
    try:
        if r is not None:
            a, b = r  # tpyc: ok
            print("finally", a, b)
    finally:
        print("finally done")


# `and`-narrow: the unpack sits under a compound condition
def use_and(r: tuple[float, int32] | None) -> None:
    if r is not None and r[1] > 0:
        a, b = r  # tpyc: ok
        print("and", a, b)
    else:
        print("and skip")


# `_` discard target
def use_discard(r: tuple[float, int32] | None) -> None:
    if r is not None:
        a, _ = r  # tpyc: ok
        print("discard", a)


# reused targets: the second unpack assigns into the existing names
def use_reused(r: tuple[float, int32] | None,
               s: tuple[float, int32] | None) -> None:
    if r is not None:
        a, b = r  # tpyc: ok
        print("reused", a, b)
        if s is not None:
            a, b = s  # tpyc: ok
            print("reused", a, b)


# str element: the view target aliases the tuple's owned element
def use_str(r: tuple[str, int32] | None) -> None:
    if r is not None:
        a, b = r  # tpyc: ok
        print("str", a, b)


# inverse: an un-narrowed whole read into an Optional slot -- no deref
def whole(r: tuple[float, int32] | None) -> None:
    s: tuple[float, int32] | None = r  # tpyc: ok
    print("whole", s is None)


# early-return narrow: the unpack follows an `if r is None: return`
def use_early(r: tuple[float, int32] | None) -> None:
    if r is None:
        print("early none")
        return
    a, b = r  # tpyc: ok
    print("early", a, b)


# closure: a nested def unpacks the narrowed enclosing param
def use_closure(r: tuple[float, int32] | None) -> None:
    def inner() -> None:
        if r is not None:
            a, b = r  # tpyc: ok
            print("closure", a, b)
    inner()


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("with exit")


# `with` body
def use_with(r: tuple[float, int32] | None) -> None:
    g = Guard()
    with g:
        if r is not None:
            a, b = r  # tpyc: ok
            print("with", a, b)


# `match` arm
def use_match(r: tuple[float, int32] | None, k: int32) -> None:
    match k:
        case 1:
            if r is not None:
                a, b = r  # tpyc: ok
                print("match", a, b)
        case _:
            print("match other")


class Missing(Exception, ReturnException):
    pass


# `@error_return` body
@error_return(Missing)
def first_of(r: tuple[float, int32] | None) -> float:
    if r is not None:
        a, b = r  # tpyc: ok
        print("error_return", b)
        return a
    raise Missing


def use_error_return(r: tuple[float, int32] | None) -> None:
    try:
        a = first_of(r)
    except Missing:
        print("error_return missing")
    else:
        print("error_return", a)


def main() -> None:
    use((1.5, 3))
    use(None)
    r = find(3)
    K(None).use(r)
    K((1.5, 3)).show()
    K(None).show()
    use_finally((1.5, 3))
    use_finally(None)
    use_and((1.5, 3))
    use_and((1.5, -3))
    use_discard((1.5, 3))
    use_reused((1.5, 3), (2.5, 4))
    use_str(("x", 3))
    rs = find_str(3)
    use_str(rs)
    whole(r)
    whole(None)
    # the `r = find(k)` idiom itself, over both return paths
    print("return", find(0) is None)
    r1 = find_name(5)
    if r1 is not None:
        a, b = r1  # tpyc: ok
        print("return", a, b)
    r2 = find_str_name(6)
    if r2 is not None:
        a2, b2 = r2  # tpyc: ok
        print("return", a2, b2)
    use_early((1.5, 3))
    use_early(None)
    use_closure((1.5, 3))
    use_with((1.5, 3))
    use_match((1.5, 3), 1)
    use_match((1.5, 3), 2)
    use_error_return((1.5, 3))
    use_error_return(None)


main()
