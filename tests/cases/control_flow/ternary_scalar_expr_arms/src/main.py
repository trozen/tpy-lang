# A value-repr Optional ternary takes any scalar-VALUED arm under the spelled
# optional wrap, and an isinstance-narrowed ternary now also carries a str
# result (the owned/view decision belongs to the sink, not to either arm).
from tpy import int32


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class B:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


def double(k: int32) -> int32:
    return k * 2


def maybe_sum(k: int32, c: bool) -> int32:
    x: int32 | None = (k + 2) if c else None    # tpyc: ok -- a binop arm
    if x is not None:
        return x
    return -1


def maybe_call(k: int32, c: bool) -> int32:
    y: int32 | None = double(k) if c else None  # tpyc: ok -- a call arm
    if y is not None:
        return y
    return -1


def tag(u: A | B) -> str:
    return "a" if isinstance(u, A) else "b"    # tpyc: ok -- a str result


def main() -> None:
    print(maybe_sum(1, True), maybe_sum(1, False))
    print(maybe_call(3, True), maybe_call(3, False))
    print(tag(A(1)), tag(B(2)))


main()
