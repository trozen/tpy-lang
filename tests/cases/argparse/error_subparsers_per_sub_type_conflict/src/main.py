# Macro-time error: same per-sub field name used in two sub-parsers
# with different field types (one yields a string, the other a
# fixed-width int). Under Option B the top record needs a single
# Optional[T] per name, so unifying these is rejected.
from argparse import ArgumentParser
from tpy import Int32

parser = ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
a = sub.add_parser("a")
a.add_argument("--x")            # str
b = sub.add_parser("b")
b.add_argument("--x", type=Int32)  # Optional[Int32]

args = parser.parse_args([])  # tpyc: error(/conflicting field types across sub-parsers/)
