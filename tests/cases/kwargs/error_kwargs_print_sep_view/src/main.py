# A COMPUTED sep= source is hoisted into a temp bound by reference, because the
# chain repeats the separator once per gap and an inline render would evaluate
# it N-1 times. A StrView-typed computed source is excluded from that: the
# hoisted binding lives as long as the chain, while the view may point into the
# full expression that ends with the statement.
from tpy import StrView


def main() -> None:
    sv: StrView = "--|--"
    print(1, 2, sep=sv[0:2])  # tpyc: error(/not yet supported/)


main()
