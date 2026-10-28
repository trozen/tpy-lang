# `list[StrView].extend` keeps a copy of each view, so a getter read off a
# temporary handing it the temporary's views is refused (Box twin: stub_arg_off_temporary).
from tpy import Own, StrView


class H:
    text: str
    views: list[StrView]

    def __init__(self) -> None:
        self.text = "abcdef"
        self.views = []

    @property
    def vw(self) -> list[StrView]:
        return self.views


def mk() -> Own[H]:
    return H()


def main() -> None:
    vs: list[StrView] = []
    # A copy of a view is the view; the temporary dies here.
    vs.extend(mk().vw)  # tpyc: error(/arg\.lends_from_temporary/)
    print(len(vs))


main()
