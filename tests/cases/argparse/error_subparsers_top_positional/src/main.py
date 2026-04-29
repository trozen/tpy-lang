# Macro-time error: subparsers cannot coexist with top-level
# positional arguments (v1 limitation; the regex-based positional
# matcher would be needed to disambiguate where the subcommand
# starts).
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("name")
sub = parser.add_subparsers(dest="cmd")  # tpyc: error(/not supported when the parser also has positional/)
