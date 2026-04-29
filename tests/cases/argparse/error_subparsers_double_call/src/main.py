# Macro-time error: add_subparsers() called twice on the same parser.
from argparse import ArgumentParser

parser = ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
sub2 = parser.add_subparsers(dest="cmd2")  # tpyc: error(/add_subparsers.*can only be called once/)
