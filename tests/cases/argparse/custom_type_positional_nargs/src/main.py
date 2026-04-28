# Variadic positional with type=<custom>: each positional token is
# routed through Tag.from_arg, the field lands as list[Tag] (not
# Optional[list[Tag]] -- nargs="+" guarantees at least one).
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
    parser.add_argument("paths", type=Tag, nargs="+")
    args = parser.parse_args(["src/main.py", "src/util.py", "tests/main.py"])
    print(args.paths)


main()
