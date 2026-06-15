# An empty list/dict literal coerced into the recursive-union JsonValue
# param of json.dumps serializes as [] / {}, including when nested inside
# another container (no JsonValue import; literals are freshly built and
# consumed by dumps, so copy semantics at the boundary are intended). The
# annotated-local path (x: JsonValue = []) is covered, CPython-compatibly,
# by union/recursive_union_empty_list.
import json


def main() -> None:
    print(json.dumps([]))
    print(json.dumps({}))
    print(json.dumps([[], {}]))
    print(json.dumps({"a": [], "b": {}}))
    print(json.dumps([1, [], 3]))


main()
