# A ctor call matching none of the OSError-family overloads is rejected with
# the No-matching-overload diagnostic (regression guard: the bare-name
# construction fallback used to skip validation and emit silently-narrowing
# C++ for an int64 errno).
from tpy import int64


def main() -> None:
    n: int64 = 99999999999
    e = OSError(n, "msg")  # tpyc: error(/No matching overload/)
    print(e)


main()
