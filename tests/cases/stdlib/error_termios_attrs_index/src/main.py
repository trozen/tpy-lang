# termios.tcgetattr returns an opaque record, not CPython's list: indexing it
# to read or edit a flag is a compile error (the divergence is documented in
# docs/LANGUAGE_FEATURES.md, "`termios` / `tty` modules").
import os
import termios


def main() -> None:
    master, slave = os.openpty()
    attrs = termios.tcgetattr(slave)
    print(attrs[3])  # tpyc: error(/Cannot index type TermAttributes: no __getitem__/)


main()
