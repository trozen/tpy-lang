# Bare-generic isinstance narrows a json.loads() result to its dict member
# -- the idiomatic CPython spelling for consuming untyped JSON. Mutating the
# narrowed dict and re-dumping proves it aliases the parsed object.
import json


def main() -> None:
    d = json.loads('{"a": 1, "b": 2}')
    if isinstance(d, dict):
        d["c"] = 3
        print(json.dumps(d, sort_keys=True))


main()
