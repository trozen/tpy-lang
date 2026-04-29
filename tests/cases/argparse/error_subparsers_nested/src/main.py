# Macro-time error: nested add_subparsers() inside a sub-parser is
# not supported in v1 (the top-level synth assumes a flat
# top -> sub* shape).
from argparse import ArgumentParser

parser = ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
a = sub.add_parser("a")
a.add_subparsers(dest="inner")  # tpyc: error(/nested add_subparsers/)
