# A container ELEMENT read at a print argument picks its own printer kind --
# set and dict elements each render through theirs.
from tpy import int32


def show(a: dict[str, set[int32]], b: dict[str, dict[str, int32]]) -> None:
    print(a["s"])
    print(b["d"])


def main() -> None:
    show({"s": {1}}, {"d": {"k": 2}})


main()
