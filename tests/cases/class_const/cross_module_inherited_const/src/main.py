# Cross-module + Phase 6 MRO walk: importing only `Child` from `limits`,
# accessing the constant inherited from `Parent`. The codegen must qualify
# the declaring ancestor's namespace even though `Parent` was never imported
# into this module.
from limits import Child


def main() -> None:
    print(Child.LIMIT)
    c = Child()
    print(c.LIMIT)


main()
