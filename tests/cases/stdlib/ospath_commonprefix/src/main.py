# os.path.commonprefix -- character-level (not path-aware) common prefix.
# Also exercises the `import os` -> os.path attribute form. Byte-compared
# against CPython posixpath.commonprefix in the cpy phase.
import os
from os import path


def main():
    print("[" + os.path.commonprefix(["/usr/lib", "/usr/local"]) + "]")
    print("[" + path.commonprefix(["abc", "abd", "abe"]) + "]")
    print("[" + os.path.commonprefix(["abc", "abc"]) + "]")
    print("[" + os.path.commonprefix(["x"]) + "]")
    print("[" + os.path.commonprefix([]) + "]")
    print("[" + os.path.commonprefix(["", "abc"]) + "]")
    # Documented quirk: char-level, so this is "/usr/l", not "/usr".
    print("[" + os.path.commonprefix(["/usr/lib", "/usr/libexec"]) + "]")


main()
