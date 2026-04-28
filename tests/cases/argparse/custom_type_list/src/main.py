# Custom ArgType with list-typed actions. The macro routes each
# token through T.from_arg and accumulates into list[T] (or
# Optional[list[T]] when no default is given) -- same path as the
# built-in types, just with a different value-coercion.
from __future__ import annotations
from argparse import ArgumentParser
from tpy import Own


class Tag:
    raw: str

    def __init__(self, raw: str) -> None:
        self.raw = raw

    @staticmethod
    def from_arg(s: str) -> Own[Tag]:
        return Tag(s)

    def __repr__(self) -> str:
        return f"Tag({self.raw})"


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--include", type=Tag, action="append")
    parser.add_argument("--paths", type=Tag, nargs="+")
    args = parser.parse_args(
        ["--include", "core", "--include", "extra",
         "--paths", "a", "b", "c"])
    # Iterate elements rather than ``print(include)``: ``args.include``
    # is ``Optional[list[Tag]]`` and TPy's print_optional wrapper for
    # nested list[T] doesn't currently unwrap to a list-formatter.
    # custom_type_positional_nargs/ exercises the ``print(list[Tag])``
    # path against a non-Optional positional list.
    include = args.include
    if include is not None:
        for t in include:
            print(t)
    print("--")
    paths = args.paths
    if paths is not None:
        for t in paths:
            print(t)


main()
