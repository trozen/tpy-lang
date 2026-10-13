# A bytearray slice READ off a call result keeps rejecting: the view would
# outlive the temporary (BUGS.md#span-slice-of-temporary-dangles).
from tpy import Own


def make_ba() -> Own[bytearray]:
    return bytearray(b"abcdefghijklmnopqrstuvwxyz")


def main() -> None:
    for c in make_ba()[1:20]:  # tpyc: error(/not yet supported by C\+\+ code generation.*slice_shape/)
        print(c)


main()
