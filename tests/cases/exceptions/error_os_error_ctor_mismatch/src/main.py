# A ctor call matching none of the OSError-family overloads is rejected with
# the No-matching-overload diagnostic (regression guard: the bare-name
# construction fallback used to skip validation and emit silently-narrowing
# C++ for an Int64 errno).
from tpy import Int64


def main() -> None:
    n: Int64 = 99999999999
    e = OSError(n, "msg")  # tpyc: error(/No matching overload/)
    print(e)


main()
