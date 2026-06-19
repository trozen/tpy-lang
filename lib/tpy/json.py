# tpy: cpp_namespace("tpystd::json")
# CPython-compatible json module.
#
# Pure-TPy wrapper over tplib.json's JsonReader/JsonWriter. Mirrors the
# CPython API surface (loads, dumps, JSONDecodeError) for untyped JSON
# values represented as a recursive union.
#
# For typed deserialization into user records, use `tplib.json` with the
# `@model` decorator -- it's faster and statically typed.
#
# Consuming a `loads` result:
#
#   loads(s) returns `Own[JsonValue]`. JsonValue is a recursive union, so
#   `d["k"]` at the use site is rejected -- not every member supports str
#   subscript, and CPython only gets away with it because its `loads`
#   returns `Any`. Narrow with a bare-generic isinstance (the CPython
#   spelling) before subscripting; the value can be consumed inline:
#
#       d = json.loads(s)             # d: JsonValue (owned)
#       if isinstance(d, dict):       # narrows d to dict[str, JsonValue]
#           print(d["users"])
#
#   `match d: case dict() as obj: ...` works the same way. For deeply
#   nested known-shape JSON, prefer `tplib.json` with the `@model`
#   decorator -- you declare a record class for the shape and parse
#   directly into it, with no per-level narrowing.
#
#   Note: the parameterized form `isinstance(d, dict[str, JsonValue])`
#   (and an alias of it) is rejected, matching CPython, which raises
#   "cannot be a parameterized generic" at runtime -- use the bare `dict`.
#
# Not yet supported (deferred):
#   - JSONEncoder / JSONDecoder classes (extension hooks)
#   - dumps kwargs: ensure_ascii (always behaves as if False -- non-ASCII
#     is emitted as raw UTF-8), separators (uses CPython defaults),
#     allow_nan, default, cls, skipkeys
#   - loads kwargs: object_hook, object_pairs_hook, parse_float, parse_int,
#     parse_constant
from tpy import Int32, Own, Readable, Writable, error_return
from tplib.json import JsonError, JsonReader, JsonToken, JsonWriter


type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list[JsonValue]
    | dict[str, JsonValue]
)


class JSONDecodeError(ValueError):
    """CPython-compatible JSON decode error.

    Thrown via normal try/except so callers don't need `@error_return`
    decoration. Carries CPython-named attributes (msg, doc, pos, lineno,
    colno). Subclasses ValueError to match CPython's hierarchy --
    `except ValueError` catches it.
    """
    msg: str
    doc: str
    pos: Int32
    lineno: Int32
    colno: Int32

    def __init__(self, msg: str, doc: str, pos: Int32) -> None:
        # `message` is BaseException's runtime field (used by __str__);
        # `msg` is CPython's documented attribute on JSONDecodeError.
        # Always equal -- both names are exposed for compatibility.
        self.message = msg
        self.msg = msg
        self.doc = doc
        self.pos = pos
        # 1-based to match CPython's JSONDecodeError.lineno/colno contract.
        line: Int32 = 1
        col: Int32 = 1
        i: Int32 = 0
        end = pos
        if end > len(doc):
            end = len(doc)
        while i < end:
            if doc[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1
        self.lineno = line
        self.colno = col


# ----------------------------------------------------------------------
# loads
# ----------------------------------------------------------------------

# Internal recursion uses JsonError directly; loads() converts to
# JSONDecodeError at the boundary. Avoids per-call try/except wrappers
# around every JsonReader operation. Own[JsonValue] return is required:
# recursive unions containing reference-type members (list/dict) need
# explicit ownership transfer to return freshly built values.
@error_return(JsonError)
def _read_value(reader: JsonReader) -> Own[JsonValue]:
    tok = reader.peek()
    if tok == JsonToken.OBJECT_START:
        d: dict[str, JsonValue] = {}
        reader.read_object_start()
        while reader.has_next():
            key = reader.read_key()
            # Intermediate local: dict subscript assignment doesn't accept
            # an Own[T] rvalue directly today. Revisit if the compiler
            # learns to auto-move Own into subscript slots.
            val = _read_value(reader)
            d[key] = val
        reader.read_object_end()
        return d
    if tok == JsonToken.ARRAY_START:
        a: list[JsonValue] = []
        reader.read_array_start()
        while reader.has_next():
            item = _read_value(reader)
            a.append(item)
        reader.read_array_end()
        return a
    if tok == JsonToken.STRING:
        return reader.read_str()
    if tok == JsonToken.NUMBER:
        raw = reader.read_number_raw()
        # Preserve BigInt precision: parse via int() unless the lexeme has
        # float syntax. CPython treats `1` and `1.0` as int and float.
        is_float = False
        i: Int32 = 0
        n = len(raw)
        while i < n:
            c = raw[i]
            if c == "." or c == "e" or c == "E":
                is_float = True
                break
            i += 1
        if is_float:
            return float(raw)
        return int(raw)
    if tok == JsonToken.TRUE:
        reader.read_bool()
        return True
    if tok == JsonToken.FALSE:
        reader.read_bool()
        return False
    if tok == JsonToken.NONE:
        reader.read_null()
        return None
    raise JsonError("Expecting value", reader.position())


def loads(s: str) -> Own[JsonValue]:
    """Deserialize a JSON document string into a JsonValue.

    Returns one of: None, bool, int (BigInt), float, str, list[JsonValue],
    dict[str, JsonValue]. Raises JSONDecodeError on malformed input or
    trailing data after the top-level value.
    """
    reader = JsonReader(s)
    try:
        value = _read_value(reader)
    except JsonError as e:
        raise JSONDecodeError(e.message, s, e.pos)
    # CPython rejects trailing non-whitespace; peek() skips whitespace and
    # returns END at EOF.
    if reader.peek() != JsonToken.END:
        raise JSONDecodeError("Extra data", s, reader.position())
    return value


# ----------------------------------------------------------------------
# dumps
# ----------------------------------------------------------------------

def _write_value(v: JsonValue, w: JsonWriter, sort_keys: bool) -> None:
    match v:
        case None:
            w.write_null()
        case bool() as b:
            w.write_bool(b)
        case int() as n:
            w.write_bigint(n)
        case float() as f:
            w.write_float(f)
        case str() as s:
            w.write_str(s)
        case list() as items:
            w.array_start()
            for item in items:
                _write_value(item, w, sort_keys)
            w.array_end()
        case dict() as d:
            w.object_start()
            keys = sorted(d.keys()) if sort_keys else list(d.keys())
            for k in keys:
                w.key(k)
                _write_value(d[k], w, sort_keys)
            w.object_end()


def dumps(obj: JsonValue, *, indent: Int32 = 0, sort_keys: bool = False) -> str:
    """Serialize a JsonValue to a JSON-formatted str.

    indent: non-zero turns on pretty-printing with that many spaces per level.
    sort_keys: when True, dict keys are emitted in sorted order.
    """
    w = JsonWriter(indent)
    _write_value(obj, w, sort_keys)
    return w.finish()


# ----------------------------------------------------------------------
# load / dump -- file-object variants over the io Readable/Writable protocols
# ----------------------------------------------------------------------

def load(fp: Readable) -> Own[JsonValue]:
    """Deserialize a JSON document from a text file object into a JsonValue.

    `fp` is any object with a `read() -> str` method (e.g. io.StringIO or
    open()'s TextIO). Equivalent to loads(fp.read()).
    """
    return loads(fp.read())


def dump(obj: JsonValue, fp: Writable, *, indent: Int32 = 0, sort_keys: bool = False) -> None:
    """Serialize a JsonValue as JSON to a text file object.

    `fp` is any object with a `write(str) -> Int32` method (e.g. io.StringIO
    or open()'s TextIO). Equivalent to fp.write(dumps(obj, ...)).
    """
    fp.write(dumps(obj, indent=indent, sort_keys=sort_keys))
