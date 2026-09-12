# A complex literal pattern -- a literal kind with no comparison codegen can
# emit. Sema's literal validation is exhaustive, so it is rejected by kind.
from tpy import int32


def classify(n: int32) -> int32:
    match n:
        case 2j:  # tpyc: error(/complex literal pattern not valid for subject type 'int32'/)
            return 20
        case _:
            return 30


def main() -> None:
    print(classify(1))


main()
