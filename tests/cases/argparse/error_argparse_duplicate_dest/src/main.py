# Two arguments resolving to the same destination would shadow each
# other in the synthesized record; rejected at macro time.
from tpy import Int32
from argparse import ArgumentParser


def main() -> Int32:
    parser = ArgumentParser()
    parser.add_argument("--name", default="x")
    parser.add_argument("--alias", dest="name", default="y")  # tpyc: error(/duplicate argument destination 'name'/)
    parser.parse_args([])
    return 0


main()
