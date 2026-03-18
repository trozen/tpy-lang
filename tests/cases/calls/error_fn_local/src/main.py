# Test that Fn type is rejected as a local variable type annotation
from tpy import Int32, Fn


def main() -> None:
    f: Fn[[Int32], Int32] = lambda x: x  # tpyc: error(/Fn type is only valid in parameter position/)


main()
