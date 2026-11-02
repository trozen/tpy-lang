# The lambda spelling of a narrower callback range-checks each call (docs/LANGUAGE_FEATURES.md "Lambda / Closures", Callable -> Fn).
from tpy import int32, int64, Fn


def cb(x: int32) -> int32:
    return x


def call64(f: Fn[[int64], int64], s: int64) -> int64:
    return f(s)


def main() -> None:
    # 3000000000 does not fit cb's int32 parameter: OverflowError, not a wrap.
    print(call64(lambda x: cb(x), 3000000000))


main()
