# A dict value read off a readonly receiver is readonly: mutating it is refused.
from tpy import int32, readonly


def touch(d: readonly[dict[str, list[int32]]]) -> None:
    d["k"].append(3)  # tpyc: error(/readonly/)


def main() -> None:
    touch({"k": [1]})


main()
