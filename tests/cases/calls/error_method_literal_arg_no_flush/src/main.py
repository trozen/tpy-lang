# The flush-less half of the rule in calls/method_literal_arg_mutable_slot: a
# container literal at a mutable method slot hoists to a named temporary, and
# where the position has no statement to hoist into it rejects instead of
# rendering a brace-init a `list[int32]&` cannot bind. Binding the borrowed
# result is such a position -- the declaration lowers the call without a flush
# -- so the literal has nowhere to go.
from tpy import int32


class K:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    # returns the parameter, so the slot stays a mutable reference
    def pick(self, xs: list[int32]) -> list[int32]:
        return xs


def main() -> None:
    k = K()
    got = k.pick([3, 4])  # tpyc: error(/not yet supported.*arg_shape/)
    print(len(got))


main()
