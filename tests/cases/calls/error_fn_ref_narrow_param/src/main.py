# A narrower callback parameter is refused, not truncated (docs/LANGUAGE_FEATURES.md "Lambda / Closures", Callable -> Fn).
from tpy import int32, int64, Fn


def cb(x: int32) -> int32:
    return x


def call_fn(f: Fn[[int64], int64], s: int64) -> int64:
    return f(s)


def main() -> None:
    s: int64 = 3000000000
    print(call_fn(cb, s))  # tpyc: error(/'cb' cannot be passed as Fn\[\[int64\], int64\]: its parameter 'x' is int32 and would receive int64 \(write 'lambda x: cb\(x\)'/)


main()
