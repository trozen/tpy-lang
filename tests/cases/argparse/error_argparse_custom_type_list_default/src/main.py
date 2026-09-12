# List defaults for type=<custom> are rejected: CPython keeps the
# default's elements as strings (no type= coercion for list defaults),
# while TPy needs them typed as T to fit a list[T] field. Rather
# than diverge in v1, we reject. Users can omit default= to get
# Optional[list[T]] or rely on the action's empty-list default.
from __future__ import annotations
from tpy import Own, int32
from argparse import ArgumentParser


class Tag:
    raw: str

    def __init__(self, raw: str) -> None:
        self.raw = raw

    @staticmethod
    def from_arg(s: str) -> Own[Tag]:
        return Tag(s)


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--tags", type=Tag, action="append", default=["a"])  # tpyc: error(/type=<custom> with default= requires a string literal/)
    parser.parse_args([])
    return 0


main()
