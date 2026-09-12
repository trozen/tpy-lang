# channel() rejects a capacity < 1 at runtime (no compile-time const check).
from tpy import int32
from tplib.channel import channel


def main() -> None:
    tx, rx = channel[int32](0)


main()
