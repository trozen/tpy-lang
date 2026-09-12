# Custom ArgType only accepts string defaults: the macro routes the
# literal through T.from_arg(<str>) at parse-fn entry (mirrors
# CPython's "string defaults run through type=" rule). A non-string
# default would require macro-time evaluation of an arbitrary
# T-valued expression, which v1 doesn't do.
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
    parser.add_argument("--tag", type=Tag, default=42)  # tpyc: error(/type=<custom> with default= requires a string literal/)
    parser.parse_args([])
    return 0


main()
