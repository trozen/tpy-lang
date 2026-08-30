# An int literal bound to a str field in a class pattern. The field literal is
# validated against the FIELD's type, not the subject's, so the mismatch is a
# located sema error instead of an ill-formed `subject.s == 3` comparison.
from dataclasses import dataclass


@dataclass
class P:
    s: str


def describe(p: P) -> str:
    match p:
        case P(s=3):  # tpyc: error(/int literal pattern not valid for field 's' of type 'str'/)
            return "three"
        case _:
            return "other"


def main() -> None:
    pass


main()
