# An owned str field returned into a union's view member: valid (the field
# outlives the call), refused until sema checks the view member's lifetime
# (BUGS.md#view-return-wrapped-slot-unchecked); the fix flips this case.
from tpy import StrView, int32


class Box:
    text: str

    def __init__(self, text: str) -> None:
        self.text = text

    def view(self) -> int32 | StrView:
        # The refused return: a view of the receiver's own buffer.
        return self.text  # tpyc: error(/return\.union_view_insert/)


def main() -> None:
    b = Box("hello")
    q = b.view()
    if isinstance(q, StrView):
        print(q)


main()
