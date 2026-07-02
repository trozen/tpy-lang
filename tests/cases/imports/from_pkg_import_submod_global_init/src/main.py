# `from pkg import submod` must run the submodule's __tpy_init so its module-global
# constants initialize; the `as`-aliased form is the sibling.
from geo import hexcodec
from geo import units as u


def main() -> None:
    print(hexcodec.hex_byte(58).decode())   # ':' -> %3A, needs _HEX initialized
    print(hexcodec.offset_sum())            # needs the top-level-built list
    print(u.scaled(3))                       # aliased submodule global


main()
