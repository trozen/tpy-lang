# os.path.normpath -- POSIX dot/dotdot collapsing and leading-slash rules.
# Byte-compared against CPython posixpath.normpath in the cpy phase.
import os.path


def main():
    cases = [
        "",
        ".",
        "..",
        "a//b/../c/",
        "a/./b",
        "/../../a",
        "//x/y",       # two leading slashes are preserved (POSIX)
        "///x/y",      # three-plus collapse to one
        "/a/b/../../c",
        "a/b/../../../d",
        "foo/bar/..",
        "/",
        "./a/./b/./",
        "../../x",
        "a/..",
    ]
    for c in cases:
        print(os.path.normpath(c))


main()
