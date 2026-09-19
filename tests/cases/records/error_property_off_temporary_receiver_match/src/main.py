# The MATCH-SUBJECT position of a view-returning @property read off a
# TEMPORARY receiver. The subject is bound for the whole match, so admitting it
# would hold a dangling `std::string_view` into the dead `mk()` temporary
# across every arm. The sink table is in docs/PROPERTY_DESIGN.md.
from tpy import Own, StrView


class H:
    s: str

    def __init__(self) -> None:
        self.s = "abcdef"

    @property
    def head(self) -> StrView:
        return self.s[0:3]


def mk() -> Own[H]:
    return H()


def main() -> None:
    match mk().head:  # tpyc: error(/match_subject.lends_from_temporary/)
        case "abc":
            print("abc")
        case _:
            print("other")


main()
