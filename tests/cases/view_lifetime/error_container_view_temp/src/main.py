# Storing a temporary rvalue into a view-typed container element is rejected
# (audit #10 item 5 / G14): the owned temporary from make() dies at
# end-of-statement, leaving the container -- which outlives the statement -- a
# dangling view. (insert/add are handled identically in codegen; param/local
# sources and owned list[str] containers are allowed -- see local_view_safe.)
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def via_append() -> None:
    xs: list[StrView] = []
    xs.append(make())  # tpyc: error(/temporary in 'append'/)
    print(len(xs))


def main() -> None:
    via_append()


main()
