# An augmented assignment through a borrow-declared METHOD call would run
# the call twice (target read and write); it is refused like the free form
# until the receiver is bound once (BUGS.md#augassign-call-receiver-double-eval).
from tpy import int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    d = {"a": P(1)}
    fb = P(2)
    # the target holds dict.get(key, default), which lends `d` and `fb`
    d.get("a", fb).n += 1  # tpyc: error(/'get\(\.\.\.\)' is evaluated twice by an augmented assignment/)
    print(d["a"].n)


main()
