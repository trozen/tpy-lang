# An inferred (un-annotated) local whose view borrows a TEMPORARY promotes to
# an owned copy (audit #10 item 3): `s = make().strip()` materializes
# std::string(str_strip(make())) rather than a dangling string_view of the
# destroyed temporary. The cpy phase is the regression guard -- a dangling view
# would print garbage and diverge from CPython's owned-string value. Bytes
# slice-of-temporary is the sibling.


def make() -> str:
    return "   padded long string that dodges the small-string buffer   "


def make_bytes() -> bytes:
    return b"   padded long bytes that dodge the small buffer here   "


def main() -> None:
    s = make().strip()          # tpyc: type(str)
    print(s)
    print(len(s))
    s2 = make()[3:9]            # tpyc: type(str)
    print(s2)
    b = make_bytes()[3:9]       # tpyc: type(bytes)
    print(len(b))
    b2 = make_bytes().strip()   # tpyc: type(bytes)
    print(len(b2))


main()
