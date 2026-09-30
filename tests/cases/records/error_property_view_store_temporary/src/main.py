# A VIEW-returning @property read off a TEMPORARY receiver, stored into a field.
# A view is a VALUE whose payload points into the receiver, so the value-category
# question rightly calls it an rvalue while the lifetime question must still say
# it borrows -- asking the category alone is how this compiled to
# `this->v = mk().view();` and kept a dangling `string_view` in the record.
# The sibling positions and their tags are listed in
# records/error_property_off_temporary_receiver_arg.
# WORKAROUND: bind the receiver first, or have the getter return `str`.
from tpy import Own, StrView, int32


class H:
    label: str

    def __init__(self) -> None:
        self.label = "ab"

    @property
    def view(self) -> StrView:
        return self.label


def mk() -> Own[H]:
    return H()


class Keeper:
    v: StrView
    n: int32

    def __init__(self) -> None:
        self.v = ""
        self.n = 0

    def take(self) -> None:
        self.v = mk().view  # tpyc: error(/Cannot bind StrView field 'v' to a temporary view source/)


def main() -> None:
    k = Keeper()
    k.take()
    print(k.v)


main()
