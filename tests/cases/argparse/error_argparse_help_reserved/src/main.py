# -h / --help is reserved by the auto-generated help printer; the
# add_help=False escape hatch isn't supported yet, so re-using the
# names is rejected at macro time.
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument("--help", action="store_true")  # tpyc: error(/-h \/ --help is reserved/)
    args = parser.parse_args([])
    return 0


main()
