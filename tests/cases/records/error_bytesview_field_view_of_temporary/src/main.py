# A BytesView field set from a slice of fresh bytes keeps a view of a
# temporary that dies with the statement, so the write rejects.
from tpy import BytesView


def mk() -> bytes:
    return b"abcdef"


class Blob:
    view: BytesView

    def __init__(self) -> None:
        # The slice's base is the fresh result of mk().
        self.view = mk()[1:3]  # tpyc: error(/Cannot bind BytesView field .view. to a temporary view source/)


def main() -> None:
    print(len(Blob().view))


main()
