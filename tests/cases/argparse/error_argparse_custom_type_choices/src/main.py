# Custom ArgType with choices= is rejected at macro time. choices=
# would need an equality predicate on the user type plus a way to
# render macro-time literals in the matched-set check; v1 punts.
from __future__ import annotations
from tpy import Own, Int32
from argparse import ArgumentParser


class Tag:
    raw: str

    def __init__(self, raw: str) -> None:
        self.raw = raw

    @staticmethod
    def from_arg(s: str) -> Own[Tag]:
        return Tag(s)


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--tag", type=Tag, choices=("a", "b"))  # tpyc: error(/type=<custom> does not support choices=/)
    parser.parse_args([])
    return 0


main()
