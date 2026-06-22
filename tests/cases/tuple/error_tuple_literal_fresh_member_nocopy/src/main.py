# A fresh tuple LITERAL with a @nocopy reference member into owned container
# storage is a clean error -- including at the member's LAST USE (codegen
# copies the member via tuple_to_storage, never moves it, so no auto-move
# exemption applies and a raw g++ deleted-ctor must not slip through).
from tpy import Int32
from tplib.box import Box


def f() -> None:
    b = Box(5)
    xs: list[tuple[Int32, Box[Int32]]] = [(1, b)]  # tpyc: error(/cannot copy non-copyable type 'Box\[Int32\]' into owned storage/)
    print(len(xs))


def main() -> None:
    f()


main()
