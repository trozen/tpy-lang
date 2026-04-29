# Macro-time error: parse_args() called without any add_parser()
# registrations on the subparsers action. There's nothing to
# dispatch to, so the synth would produce an unreachable record.
from argparse import ArgumentParser

parser = ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
args = parser.parse_args([])  # tpyc: error(/at least one add_parser/)
