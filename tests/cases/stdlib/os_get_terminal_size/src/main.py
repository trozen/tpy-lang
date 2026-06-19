# os.get_terminal_size -> terminal_size (columns/lines). In a non-tty (CI) the
# ioctl fails and both TPy and CPython raise OSError; a real terminal returns
# positive dimensions (machine-specific). Checked so output is deterministic in
# CI. Byte-compared against CPython.
import os


def main():
    try:
        ts = os.get_terminal_size()
        print(ts.columns > 0 and ts.lines > 0)
    except OSError:
        print("not a tty")


main()
