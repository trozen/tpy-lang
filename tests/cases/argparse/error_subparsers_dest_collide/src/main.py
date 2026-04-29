# Macro-time error: subparsers' dest= equals a top-level common
# arg's dest. The top record can't have two fields named the same.
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("--cmd")  # top-level dest=cmd
sub = parser.add_subparsers(dest="cmd")  # tpyc: error(/subparsers dest='cmd' collides/)
