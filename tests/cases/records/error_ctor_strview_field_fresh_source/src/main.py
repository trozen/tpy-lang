# A StrView field cannot keep a freshly built str: the view would outlive the
# concatenation it points into, so the constructor rejects it.
from tpy import StrView


class Label:
    view: StrView

    def __init__(self, a: str) -> None:
        self.view = a + "!"  # tpyc: error(/ctor.mil_field.nominal.binop/)


def main() -> None:
    print(Label("q").view)


main()
