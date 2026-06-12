# A comprehension element reaching an @error_return callable -- free function,
# method, or property getter (hidden outside children()) -- falls back to the
# inline vector path: the unwrap's return/goto would mistarget from inside the
# array_from_index lambda. Propagation must still work from the element expr.
from tpy import Int32, error_return, ReturnException


class MyErr(Exception, ReturnException):
    pass


class Gauge:
    raw: Int32

    def __init__(self, raw: Int32) -> None:
        self.raw = raw

    @property
    @error_return(MyErr)
    def val(self) -> Int32:
        if self.raw < 0:
            raise MyErr()
        return self.raw

    @error_return(MyErr)
    def scaled(self, k: Int32) -> Int32:
        if self.raw < 0:
            raise MyErr()
        return self.raw * k


@error_return(MyErr)
def half(n: Int32) -> Int32:
    if n % 2 != 0:
        raise MyErr()
    return n // 2


@error_return(MyErr)
def halves() -> Int32:
    hs = [half(i) for i in range(0, 8, 2)]  # tpyc: type(/list\[Int32\]/)
    return hs[0] + hs[3]


@error_return(MyErr)
def bad() -> Int32:
    hs = [half(i) for i in range(3)]  # tpyc: type(/list\[Int32\]/)
    return hs[0]


def main():
    try:
        print(halves())
    except MyErr:
        print("unexpected")
    try:
        print(bad())
    except MyErr:
        print("propagated")
    g = Gauge(5)
    try:
        props = [g.val for i in range(4)]  # tpyc: type(/list\[Int32\]/)
        meths = [g.scaled(i) for i in range(3)]  # tpyc: type(/list\[Int32\]/)
        print(props[0], len(props), meths[2])
    except MyErr:
        print("unexpected")
    bad_g = Gauge(-1)
    try:
        broken = [bad_g.val for i in range(2)]
        print(broken[0])
    except MyErr:
        print("caught")


main()
