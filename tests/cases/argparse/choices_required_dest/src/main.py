# argparse choices= / required= / dest= / help= (Phase 7D4).
from tpy import int32
from argparse import ArgumentParser


def main() -> int32:
    parser = ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("fast", "slow"),
        required=True,
        help="execution mode",
    )
    parser.add_argument("--level", type=int, default=0,
                        choices=(0, 1, 2),
                        dest="severity")
    args = parser.parse_args(["--mode", "fast", "--level", "2"])
    print(args.mode)
    print(args.severity)
    return 0


main()
