# A tuple LITERAL with a @nocopy reference member still read afterwards, into
# owned container storage: the member has no copy to make, a clean error (at
# its last use the member moves in, ownership_record_renders).
from tpy import int32
from tplib.box import Box


def f() -> None:
    b = Box(5)
    xs: list[tuple[int32, Box[int32]]] = [(1, b)]  # tpyc: error(/cannot copy non-copyable type 'Box\[int32\]' into owned storage/)
    print(len(xs), b.get())


def main() -> None:
    f()


main()
