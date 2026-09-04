# A guarded arm in the Optional-subject inner CHAIN: the chain has no per-arm
# block to fall out of, so the guard folds into the arm's own condition
# (`inner == "a" && flag`). The neighbouring guarded tier, picked when an arm
# is labelless or binds, keeps its standalone-if + goto render instead.
from typing import Iterator, Optional


def folded(x: Optional[str], flag: bool) -> str:
    match x:
        case None:
            return "none"
        case "a" if flag:  # the guard folds into this arm's condition
            return "a-flag"
        case "a":
            return "a"
        case _:
            return "other"


def folded_or(x: Optional[str], flag: bool) -> str:
    match x:
        case None:
            return "none"
        case "a" | "b" if flag:  # the or-group needs parens before the `&&`
            return "ab-flag"
        case "a" | "b":
            return "ab"
        case _:
            return "other"


def folded_later(x: Optional[str], flag: bool) -> str:
    match x:
        case "a":
            return "a"
        case "b" if flag:  # arm position does not change the fold
            return "b-flag"
        case _:
            return "other"


def wildcard_guard(x: Optional[str], flag: bool) -> str:
    # A labelless guarded arm has no condition to fold into: this routes to
    # the standalone-if + goto tier, not the chain.
    match x:
        case None:
            return "none"
        case "a":
            return "a"
        case _ if flag:
            return "wild-flag"
        case _:
            return "other"


def binding_guard(x: Optional[str], flag: bool) -> str:
    # A binding is written after the arm's `if`, so a folded guard could not
    # read it -- the goto tier takes this one too.
    match x:
        case None:
            return "none"
        case "a" as got if flag:
            return "got:" + got
        case "a":
            return "a"
        case _:
            return "other"


def gen(x: Optional[str], flag: bool) -> Iterator[str]:
    # The same fold inside a resumable, where the arm bodies are frame blocks.
    match x:
        case None:
            yield "none"
        case "a" if flag:
            yield "a-flag"
        case "a":
            yield "a"
        case _:
            yield "other"


def main() -> None:
    print(folded("a", True), folded("a", False), folded(None, True),
          folded("z", True))
    print(folded_or("b", True), folded_or("b", False), folded_or(None, False))
    print(folded_later("b", True), folded_later("b", False),
          folded_later("a", True))
    print(wildcard_guard("z", True), wildcard_guard("z", False))
    print(binding_guard("a", True), binding_guard("a", False))
    for v in gen("a", True):
        print(v)
    for v in gen("a", False):
        print(v)


main()
