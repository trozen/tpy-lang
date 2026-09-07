# A negated literal wider than int32 at a recursive-union element slot takes the
# width-pinned ctor spelling, which the element fold does not reproduce.
import json


def main() -> None:
    print(json.dumps([-4294967296]))  # tpyc: error(/method.qualcall.arg.union/)


main()
