# Runtime OSError -- raised when reading from a file opened for writing
# only, or writing to a file opened for reading only. CPython routes these
# through `io.UnsupportedOperation` (which inherits from both OSError and
# ValueError); TPy doesn't support diamond inheritance so we route to OSError
# alone -- catchable as `except OSError` (won't be caught by `except ValueError`,
# a documented divergence from CPython). Messages are TPy-specific (longer than
# CPython's terse "not readable"/"not writable"); test carries no_cpython.txt.


def main() -> None:
    path = "tpy_test_os_error_caught.tmp"

    # Seed the file with some content for read-mode tests.
    with open(path, "w") as f:
        f.write("seed content\n")

    # File opened write-only; read() should raise OSError.
    with open(path, "w") as f:
        try:
            f.read()
        except OSError as e:
            print("read on write-only:", str(e))

    # File opened read-only; write() should raise OSError.
    with open(path, "r") as f:
        try:
            f.write("xyz")
        except OSError as e:
            print("write on read-only:", str(e))

    # readline() on write-only file.
    with open(path, "w") as f:
        try:
            f.readline()
        except OSError as e:
            print("readline on write-only:", str(e))


main()
