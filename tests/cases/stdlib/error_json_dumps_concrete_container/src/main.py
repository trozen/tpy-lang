# A concrete container *variable* (dict[str, Int32]) cannot be converted
# element-wise into the recursive-union JsonValue: std containers have no
# element-converting constructor. TPy rejects it with a clean diagnostic
# (rather than emitting an ill-formed C++ deep conversion). Workaround:
# annotate the value as JsonValue, or pass a literal. Deep element-wise
# container->wrapper conversion is tracked in BUGS.md.
import json


def main() -> None:
    d = {"a": 123}
    print(json.dumps(d))  # tpyc: error(/expected JsonValue, got dict\[str, Int32\]/)


main()
