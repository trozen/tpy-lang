# os.path v1 -- pure-string path manipulation (POSIX). Output is byte-compared
# against CPython's posixpath in the cpy phase, so this doubles as a parity test.
from os.path import (
    join, split, splitext, basename, dirname, isabs, splitdrive,
    sep, extsep, pardir, curdir, pathsep, defpath, devnull,
)


def show2(pair: tuple[str, str]) -> None:
    print(pair[0] + " | " + pair[1])


def main():
    # join: relative, absolute-reset, trailing slash, empty first/component
    print(join("/usr", "lib", "foo.py"))      # tpyc: ok
    print(join("/usr", "/etc", "x"))
    print(join("a", "b"))
    print(join("a/", "b"))
    print(join("", "b"))
    print(join("a", ""))
    print(join("/single"))

    # split / dirname / basename over the tricky slash forms
    for p in ["/a/b", "a/b", "a", "/", "//", "a/", "", "/a/b/"]:
        show2(split(p))
        print(dirname(p) + " <> " + basename(p))

    # splitext: leading dots, multi-dot, no-ext, trailing dot
    for q in ["foo.txt", "foo.tar.gz", ".bashrc", "/a/.bashrc",
              "foo", "a.", "/a/b", "..ext", "/d.ir/file"]:
        show2(splitext(q))

    # isabs / splitdrive (POSIX: drive is always empty)
    print(isabs("/x"))
    print(isabs("x"))
    print(isabs(""))
    show2(splitdrive("/a/b"))

    print(sep + extsep + pardir + curdir + pathsep)
    print(defpath)
    print(devnull)


main()
