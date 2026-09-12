# Test that Fn type is rejected as a local variable type annotation
from tpy import int32, Fn


def main() -> None:
    f: Fn[[int32], int32] = lambda x: x  # tpyc: error(/Fn type is only valid in parameter position/)


main()
