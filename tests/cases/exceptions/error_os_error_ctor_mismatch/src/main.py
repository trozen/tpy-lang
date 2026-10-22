# A ctor call matching none of the OSError-family overloads is rejected with
# the No-matching-overload diagnostic (no construction fallback skips it).


def main() -> None:
    e = OSError(1.5, "msg")  # tpyc: error(/No matching overload/)
    print(e)


main()
