# A bytes literal pattern in a match over an integer subject. Pins the sema
# rejection by literal kind, which is where the mismatch is first caught.
from tpy import int32


def classify(n: int32) -> int32:
    match n:
        case 1:
            return 10
        case b"abc":  # tpyc: error(/bytes literal pattern not valid for subject type 'int32'/)
            return 20
        case _:
            return 30


def main() -> None:
    print(classify(1))


main()
