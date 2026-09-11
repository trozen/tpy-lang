# Two modules delegating to each other: each frame would embed the other by
# value, so the pair is infinite-size however the names are spelled. The
# within-module emit-order sort cannot see the cycle, so the field-type
# decision rejects it directly -- with the same diagnostic.
import moda


def main() -> None:
    for v in moda.a(3):
        print(v)


main()
