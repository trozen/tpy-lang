# An OWNING call's container result handed straight to a mutable method
# parameter: the rvalue cannot bind the `list[int32]&` slot and the method
# family has no hoisting row for a call rvalue, so it rejects at the call's
# return type. Binding the result to a local first compiles.
from tpy import Own, int32


class K:
    def fill(self, xs: list[int32]) -> int32:
        xs.append(9)
        return len(xs)


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    k = K()
    print(k.fill(mk()))  # tpyc: error(/call\.ret_type\.container/)


main()
