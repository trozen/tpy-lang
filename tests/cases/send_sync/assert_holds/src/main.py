# assert_send[T]() / assert_sync[T]() are zero-cost compile-time trait checks.
# They emit no code, and run as no-ops under CPython.
from tpy import Int32, assert_send, assert_sync


class Trade:
    qty: Int32

    def __init__(self, qty: Int32) -> None:
        self.qty = qty


def main() -> None:
    assert_send[Int32]()        # tpyc: ok
    assert_sync[Int32]()        # tpyc: ok
    assert_send[Trade]()        # tpyc: ok
    assert_send[list[Int32]]()  # tpyc: ok
    t = Trade(5)
    print(t.qty)


main()
