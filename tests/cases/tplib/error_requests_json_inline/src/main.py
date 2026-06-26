# A json= body passed as an inline dict literal is rejected: the compiler will
# not implicitly convert a concrete dict[str, str] into the recursive-union
# JsonValue (it would be a hidden deep copy). The caller must bind a JsonValue
# local first. This guard is the documented divergence from CPython requests.
import tplib.requests as requests


def main() -> None:
    requests.post("http://api.test/v1", None, {"a": "b"})  # tpyc: error(/recursive-union|JsonValue/)


main()
