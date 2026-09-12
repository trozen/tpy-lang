# `for kv in d.items()` with a SINGLE loop variable: the dict-view gate is
# tuple-unpack-only, and the container-return fallback must not swallow it.
# The list comprehension still rejects.
from tpy import int32


def main() -> None:
    d: dict[str, int32] = {"a": 1}
    xs = [kv for kv in d.items()]  # tpyc: error(/expr.list_comp/)
    print(len(xs))


main()
