# assert_sync rejects a mutable container (Send but not Sync).
from tpy import Int32, assert_sync


def main() -> None:
    assert_sync[list[Int32]]()  # tpyc: error(/assert_sync assertion failed.*mutable container/)


main()
