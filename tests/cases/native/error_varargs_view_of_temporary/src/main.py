# A binding's `*args` pack is never kept, but a binding may keep a copy of each
# element: a view of a temporary's storage as an element is refused.
from tpy import Own, StrView
from tpy.extern import native


@native("keep_views")
def keep(*xs: StrView) -> None: ...


class H:
    text: str

    def __init__(self) -> None:
        self.text = "abcdef"


def mk() -> Own[H]:
    return H()


def spread(views: list[StrView]) -> None:
    # A spread hands copies of the views it holds: accepted.
    keep(*views)


def main() -> None:
    name = "ab"
    # The temporary dies at the end of the statement; the binding keeps the view.
    keep(name, StrView(mk().text))  # tpyc: error(/arg\.lends_from_temporary/)


main()
