# A `super().__init__` message containing a WALRUS: the part it binds has no
# flush point and the parts' evaluation order is unspecified, so a later read
# of the same name would race.
from tpy import Int32


class Tagged(Exception):
    n: Int32

    def __init__(self, n: Int32) -> None:  # tpyc: error(/ctor\.base_init/)
        # The named expression binds inside the message parts.
        super().__init__("x" + str((m := n + 1)) + str(m))
        self.n = n


def main() -> None:
    try:
        raise Tagged(5)
    except Tagged as e:
        print(e.n)


main()
