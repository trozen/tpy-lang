# Positional with nargs='?' and default= -- the user's default is
# returned when the slot is absent from argv.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("name", nargs="?", default="anon")
    a1 = parser.parse_args([])
    print(a1.name)

    parser2 = ArgumentParser()
    parser2.add_argument("name", nargs="?", default="anon")
    a2 = parser2.parse_args(["alice"])
    print(a2.name)
    return 0


main()
