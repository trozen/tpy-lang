# `list([...])` over a STR element literal: the element target the call
# derives is one the instantiation arm does not substitute.
# TPy rejects the call `list(["x", "y"])` today.
def main() -> None:
    a = list(["x", "y"])  # tpyc: error(/expr\.call:call\.inst_shape/)
    print(a[0])


main()
