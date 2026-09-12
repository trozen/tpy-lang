# Builder-macro re-export through a plain module. utils.py does
# `from argparse import ArgumentParser`; main pulls it via utils.
# The @builder_macro lookup must walk the re-export chain to find
# the macro under the registry's canonical (argparse, ArgumentParser)
# key.
from tpy import int32
from utils import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("name")
    args = parser.parse_args(["alice"])
    print(args.name)
    return 0


main()
