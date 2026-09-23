# An OWNING call's container result handed straight to a mutable method
# parameter: the method family has no hoisting row for a call rvalue at a
# mutated slot, so it rejects at the argument shape -- the same stop its
# record twin `k.take(mk_rec())` and the copy spelling `k.fill(copy(d))`
# take (BUGS.md#record-rvalue-at-mutated-method-slot-unhoisted). Binding
# the result to a local first compiles.
from tpy import Own, int32


class K:
    def fill(self, xs: list[int32]) -> int32:
        xs.append(9)
        return len(xs)


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    k = K()
    print(k.fill(mk()))  # tpyc: error(/method\.arg_shape/)


main()
