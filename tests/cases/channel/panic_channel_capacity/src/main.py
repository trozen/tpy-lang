# channel() rejects a capacity < 1 at runtime (no compile-time const check).
from tpy import Int32
from tpy.channel import channel


def main() -> None:
    tx, rx = channel[Int32](0)


main()
