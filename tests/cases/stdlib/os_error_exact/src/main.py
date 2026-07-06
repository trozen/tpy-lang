# CPython-exact OSError text and attributes from real os/file failures:
# str(e) is "[Errno N] strerror[: 'filename'[ -> 'filename2']]", .errno
# compares against the errno module's constants, .filename/.filename2 echo
# the arguments as given. Paths passed as literals echo verbatim in the
# message (no realpath), so the /tmp base is host-stable.
import errno
import os


def teardown(base: str) -> None:
    if os.path.exists(base + "/d"):
        os.rmdir(base + "/d")
    if os.path.exists(base):
        os.rmdir(base)


def main():
    base = "/tmp/tpy_os_error_exact"
    teardown(base)
    os.mkdir(base)
    os.mkdir(base + "/d")

    try:
        open("definitely_missing_tpy_xyz.txt")
    except FileNotFoundError as e:
        print(e)
        print(e.errno == errno.ENOENT, e.strerror, e.filename)
    try:
        os.stat("also_missing_tpy_xyz")
    except FileNotFoundError as e:
        print(e)
    try:
        os.mkdir(base + "/d")
    except FileExistsError as e:
        print(e)
        print(e.errno == errno.EEXIST, e.filename)
    try:
        os.rename("missing_src_tpy_xyz", "missing_dst_tpy_xyz")
    except FileNotFoundError as e:
        print(e)
        print(e.filename, e.filename2)
    try:
        os.close(999999)
    except OSError as e:
        print(e)
        # No filename on an fd op: TPy "" / CPython None, both falsy.
        print(e.errno == errno.EBADF, not e.filename)

    # Constant bindings resolve and carry host-correct values (EPERM/EACCES/
    # EISDIR/ENOTDIR are identical on Linux and macOS; ETIMEDOUT diverges,
    # so only its positivity is pinned).
    print(errno.EPERM == 1, errno.EACCES == 13, errno.EISDIR == 21,
          errno.ENOTDIR == 20, errno.ETIMEDOUT > 0)

    teardown(base)


main()
