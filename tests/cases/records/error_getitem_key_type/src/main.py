# `t[k]` receives its key as the explicit t.__getitem__(k) call does
# (docs/OVERLOAD_DESIGN.md "Subscripts"): an int key at a str-keyed
# __getitem__ is the call's type mismatch.
from tpy import int32


class Scores:
    def __init__(self) -> None:
        pass

    def __getitem__(self, key: str) -> int32:
        return len(key)


def main() -> None:
    s = Scores()
    print(s[42])  # tpyc: error(/Type mismatch in argument 'key': expected str/)


main()
