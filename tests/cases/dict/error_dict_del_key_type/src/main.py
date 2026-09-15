# `del d[k]` type-checks its key against the dict's declared key type, the same
# check the read and the store take -- a str needle on a dict[int32, str] is a
# located error, not a C++ build failure.
from tpy import int32


def main() -> None:
    d: dict[int32, str] = {1: "a"}
    del d["nope"]  # tpyc: error(/Type mismatch in dict key/)
    print(len(d))


main()
