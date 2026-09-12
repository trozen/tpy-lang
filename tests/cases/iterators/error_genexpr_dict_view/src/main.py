# A dict SOURCE lowers in a genexpr, but a dict VIEW must keep rejecting:
# the lambda's init-capture would name a nested `iterator` typedef the views do
# not have, so the render would be ill-formed C++.
from tpy import int32


def main() -> None:
    d = {1: 2}
    print(sum(v for v in d.values()))  # tpyc: error(/expr.genexpr/)


main()
