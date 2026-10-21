# A tuple result with a view element has no render: its literal would build
# an owning temporary for the view (TODO.md, "a tuple literal at a view-element
# result").
from tpy import Own, StrView, int32


def f(x: Own[str]) -> tuple[StrView, int32]:
    # The owned parameter's buffer dies with the frame.
    return (x, 1)  # tpyc: error(/return\.slot_type/)


def main() -> None:
    f("ab" * 20)


main()
