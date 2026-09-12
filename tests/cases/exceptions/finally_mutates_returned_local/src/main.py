# A finally that mutates a returned reference-type local must be visible in
# the returned object (CPython aliasing); value types keep the eager capture.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


def ret_record() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        b.n += 1


def ret_list() -> Own[list[int32]]:
    xs = [1]
    try:
        return xs
    finally:
        xs.append(2)


def ret_optional(flag: bool) -> Own[Box] | None:
    b: Box | None = None
    if flag:
        b = Box()
    try:
        return b
    finally:
        if b is not None:
            b.n += 1


def ret_value_int() -> int32:
    n = 10
    try:
        return n
    finally:
        n += 1


def ret_rebound() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        b = Box()  # rebinding: the pending return keeps the original object


def ret_param_ref(b: Box) -> Box:
    try:
        return b
    finally:
        b.n += 1


def ret_own_param(b: Own[Box]) -> Own[Box]:
    try:
        return b
    finally:
        b.n += 1


def ret_from_handler() -> Own[Box]:
    b = Box()
    try:
        raise ValueError("boom")
    except ValueError:
        return b
    finally:
        b.n += 1


def ret_finally_override() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        b.n += 1
        c = Box()
        c.n = 99
        return c


def main() -> None:
    print(ret_record().n)
    print(ret_list())
    r = ret_optional(True)
    if r is not None:
        print(r.n)
    print(ret_optional(False) is None)
    print(ret_value_int())
    print(ret_rebound().n)
    shared = Box()
    print(ret_param_ref(shared).n, shared.n)
    print(ret_own_param(Box()).n)
    print(ret_from_handler().n)
    print(ret_finally_override().n)


main()
