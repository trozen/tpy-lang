# Documents the ensure_ascii deviation from CPython.
#
# CPython's json.dumps defaults to ensure_ascii=True, escaping every
# non-ASCII codepoint as \uXXXX. TPy currently emits non-ASCII as raw
# UTF-8 (i.e. always behaves as if ensure_ascii=False). This test pins
# the current behavior so a future change to _write_escaped is caught
# by a snapshot diff. Marked no_cpython because output deliberately
# differs from CPython.
import json
from json import JSONDecodeError


def main() -> None:
    try:
        # Round-trip: loads accepts non-ASCII source bytes, dumps emits
        # them unchanged. CPython by default would re-escape `é` to
        # `é` on output.
        v = json.loads('"caf\xe9"')
        print(json.dumps(v))
    except JSONDecodeError as e:
        print("ERR:", e.msg)


main()
