# json.load / json.dump over io text file objects (StringIO and open()'s
# TextIO). load(fp) == loads(fp.read()); dump(obj, fp) == fp.write(dumps(...)).
import io
import json


def load_from_stringio() -> None:
    src = io.StringIO('{"name": "tpy", "nums": [1, 2, 3], "ok": true}')
    v = json.load(src)
    print("roundtrip:", json.dumps(v, sort_keys=True))


def dump_to_stringio() -> None:
    v = json.loads('{"b": 2, "a": 1, "c": [3, 4]}')
    out = io.StringIO()
    json.dump(v, out, sort_keys=True)
    print("dumped:", out.getvalue())
    # dump with indent
    out2 = io.StringIO()
    json.dump(v, out2, indent=2, sort_keys=True)
    print("indented:")
    print(out2.getvalue())


def file_roundtrip() -> None:
    v = json.loads('{"list": [10, 20], "flag": false, "label": "x"}')
    path = "tpy_test_json_load_dump.json"
    with open(path, "w") as f:
        json.dump(v, f, sort_keys=True)
    with open(path) as f:
        v2 = json.load(f)
    print("file:", json.dumps(v2, sort_keys=True))


def load_malformed() -> None:
    # load(fp) propagates JSONDecodeError from the underlying loads(fp.read()).
    # Print lineno/colno (not msg -- CPython words messages differently).
    try:
        json.load(io.StringIO("{not valid"))
        print("FAIL: expected JSONDecodeError")
    except json.JSONDecodeError as e:
        print("decode error at:", e.lineno, e.colno)


def main() -> None:
    load_from_stringio()
    print("---")
    dump_to_stringio()
    print("---")
    file_roundtrip()
    print("---")
    load_malformed()


main()
