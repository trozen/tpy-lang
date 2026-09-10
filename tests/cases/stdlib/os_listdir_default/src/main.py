# os.listdir() with no argument defaults to the current directory (CPython
# listdir(path=".")). Builds a fresh tree in the scratch cwd, chdir into it, and lists with no
# arg. Byte-compared against CPython.
import os


def teardown(base: str) -> None:
    for n in ["a.txt", "b.txt"]:
        if os.path.exists(base + "/" + n):
            os.remove(base + "/" + n)
    if os.path.exists(base):
        os.rmdir(base)


def main():
    base = "tpy_os_listdir_default"
    teardown(base)
    os.mkdir(base)
    for n in ["a.txt", "b.txt"]:
        with open(base + "/" + n, "w") as fh:
            fh.write(n)

    cwd = os.getcwd()
    os.chdir(base)
    print(",".join(sorted(os.listdir())))       # no-arg -> cwd
    print(",".join(sorted(os.listdir("."))))    # explicit "." matches
    os.chdir(cwd)
    teardown(base)


main()
