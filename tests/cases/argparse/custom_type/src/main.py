# Custom user types in argparse via the ArgType pattern. Tag accepts
# a "[ns:]name" string -- "core:strict" parses into namespace="core",
# name="strict"; "release" leaves namespace empty. The macro detects
# records with @staticmethod from_arg(s: str) -> Self and emits
# T.from_arg(token); CPython argparse calls T(token) via __init__,
# so the class supports both factories and parses identically under
# both backends. The --label string default routes through from_arg
# at parse-fn entry, mirroring CPython's "string defaults run
# through type=" rule. The required positional `input` lands as a
# plain `Tag` (not Optional[Tag]) -- the synthesized parse fn keeps
# an Optional[T] accumulator during the loop and unwraps once the
# missing-required check has fired.
from __future__ import annotations
from argparse import ArgumentParser
from tpy import Own, Int32


class Tag:
    namespace: str
    name: str

    def __init__(self, raw: str) -> None:
        self.namespace = ""
        self.name = raw
        idx: Int32 = raw.find(":")
        if idx >= 0:
            self.namespace = raw[:idx]
            self.name = raw[idx + 1:]

    @staticmethod
    def from_arg(s: str) -> Own[Tag]:
        return Tag(s)

    def __str__(self) -> str:
        if self.namespace == "":
            return self.name
        return self.namespace + ":" + self.name


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("input", type=Tag)
    parser.add_argument("--out", type=Tag)
    parser.add_argument("--label", type=Tag, default="ci:nightly")
    args = parser.parse_args(["core:strict", "--out", "release"])
    print(args.input)
    print(args.out)
    print(args.label)


main()
