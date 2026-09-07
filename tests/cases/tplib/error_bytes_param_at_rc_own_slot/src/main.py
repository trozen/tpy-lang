# A `bytes` parameter at an `Own[bytes]` qualified-call slot: the bytes copy is
# a separate row from the str one and has no witnessed render here, so
# `Rc.new(b)` is rejected.
from tpy import Own
from tplib import Rc


def wrap_b(b: bytes) -> Own[Rc[bytes]]:
    return Rc.new(b)  # tpyc: error(/method.qualcall.arg.own/)


def main() -> None:
    print(len(wrap_b(b"xy").get()))


main()
