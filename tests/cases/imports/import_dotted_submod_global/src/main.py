# `import pkg.sub` (dotted) must also run the submodule's __tpy_init -- the sibling
# of the `from pkg import sub` init path; reads a non-Final submodule global.
import geo.codec


def main() -> None:
    print(geo.codec.hi_nibble(58))   # ':' -> _HEX[3] == '3' (51); empty _HEX would panic


main()
