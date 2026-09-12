# A WHOLE-tuple loop variable inside a simple generator (`for kv in d.items()`):
# its element is a tuple type, the storage-form tuple-local rung. TPy does not
# compile this today -- `yield kv[1]` inside the loop is rejected.
from typing import Iterator
from tpy import int32


def pairs(d: dict[str, int32]) -> Iterator[int32]:  # tpyc: error(/sgen.loop_var_type/)
    for kv in d.items():
        yield kv[1]


def main() -> None:
    for n in pairs({"a": 1}):
        print(n)


main()
