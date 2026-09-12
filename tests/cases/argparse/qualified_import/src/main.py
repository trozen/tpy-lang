# Qualified-import style: `import argparse; argparse.ArgumentParser()`
# parses as TpyMethodCall, but resolved_import still points at the
# builder class -- the expander must detect this form.
from tpy import int32
import argparse


def main() -> int32:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    args = parser.parse_args(["alice"])
    print(args.name)
    return 0


main()
