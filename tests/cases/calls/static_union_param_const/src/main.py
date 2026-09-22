# A `@staticmethod` with a record-union parameter it only READS takes the union
# with const pointees, and the call site has to build that same variant: it
# used to build the mutable one, which C++ cannot convert, so the program
# passed the front end and failed the build. A callee that WRITES through the
# union keeps the mutable variant, and the write reaches the caller's object.
from tpy import int32


class R:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Other:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


def free_read(v: R | Other) -> int32:
    match v:
        case R():
            return v.x
        case Other():
            return v.y


class K:
    # read-only union parameter
    @staticmethod
    def read(v: R | Other) -> int32:
        match v:
            case R():
                return v.x
            case Other():
                return v.y

    # the callee writes through the union
    @staticmethod
    def bump(v: R | Other) -> None:
        match v:
            case R():
                v.x += 10
            case Other():
                v.y += 100


def main() -> None:
    r = R(1)
    o = Other(2)
    print("free_read", free_read(r), free_read(o))
    print("static_read", K.read(r), K.read(o))  # tpyc: ok
    K.bump(r)  # tpyc: ok
    K.bump(o)
    print("static_bump", r.x, o.y)


main()
