# Macro-time error: a per-sub field name collides with a top-level
# common arg dest. Under the flat Option-B layout the top record
# can't carry distinct fields for both, so the macro rejects.
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("--out")  # top-level dest=out

sub = parser.add_subparsers(dest="cmd")
a = sub.add_parser("a")
a.add_argument("--out")  # collides with top-level

args = parser.parse_args([])  # tpyc: error(/per-sub argument 'out' collides with a top-level/)
