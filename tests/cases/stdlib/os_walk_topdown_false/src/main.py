# os.walk(topdown=False) is a v1 gap -- it raises NotImplementedError (TPy-only:
# CPython supports bottomup, so the divergence can't be byte-compared).
import os


def main() -> None:
    try:
        for dirpath, dirnames, filenames in os.walk("/tmp", topdown=False):
            print(dirpath)
        print("no raise")
    except NotImplementedError:
        print("topdown=False raises NotImplementedError")


main()
