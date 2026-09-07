# `if xs:` on an `Optional[list]`: builtin containers are outside the
# truthiness dunder dispatch, so the name has no admitted truthy render.
from tpy import Int32


def probe(xs: list[Int32] | None) -> bool:
    if xs:  # tpyc: error(/stmt\.if:cond\.name/)
        return True
    return False


def main() -> None:
    print(probe([1]))


main()
