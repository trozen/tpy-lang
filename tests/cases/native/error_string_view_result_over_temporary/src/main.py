# A view declared to borrow its `String` argument cannot be returned over a
# temporary one: the string dies with the statement.
from tpy import String, StrView
from tpy.extern import native


@native(result={"s": "borrow"})
def view_of(s: String) -> StrView: ...


@native
def mk() -> String: ...


def leak() -> StrView:
    return view_of(mk())  # tpyc: error(/Cannot return StrView referencing a local or temporary/)


def main() -> None:
    print(leak())


main()
