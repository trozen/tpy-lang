# A user that explicitly imports ``sys`` alongside ``argparse``: the
# argparse macro_deps wiring also brings ``sys`` into ``macro_ns``,
# but the user's ``import sys`` lands in ``global_ns`` (which has
# higher lookup priority), so resolution stays unambiguous. Both
# the user-side ``sys.argv`` read and the macro-injected
# ``sys.argv[1:]`` (in the bare ``parse_args()`` rewrite) resolve
# to the same module.
import sys
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    if len(sys.argv) > 0:
        # exercises the user's own sys.argv read
        pass
    parser = ArgumentParser()
    parser.add_argument("--name", default="world")
    args = parser.parse_args()
    print(args.name)
    return 0


main()
