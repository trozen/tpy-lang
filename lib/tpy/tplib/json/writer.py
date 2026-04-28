# JSON writer: builds a JSON string incrementally.
from tpy import Int32, Int64, Float64, Float32, Char, String

_HEX = "0123456789abcdef"

def _hex_byte(v: Int32) -> str:
    return _HEX[v // 16] + _HEX[v % 16]

class JsonWriter:
    _buf: str
    _needs_comma: bool
    _indent: Int32
    _depth: Int32
    _fresh_line: bool

    def __init__(self, indent: Int32 = 0) -> None:
        self._buf = ""
        self._needs_comma = False
        self._indent = indent
        self._depth = 0
        self._fresh_line = False

    # -- pretty-printing helpers (only called when _indent > 0) --

    def _emit_nl(self) -> None:
        self._buf += "\n"
        n = self._indent * self._depth
        if n > 0:
            self._buf += " " * n

    def _pretty_sep(self) -> None:
        if self._needs_comma:
            self._buf += ","
            self._emit_nl()
        elif self._fresh_line:
            self._emit_nl()
        self._needs_comma = False
        self._fresh_line = False

    def _pretty_open(self, bracket: str) -> None:
        self._pretty_sep()
        self._buf += bracket
        self._depth += 1
        self._fresh_line = True
        self._needs_comma = False

    def _pretty_close(self, bracket: str) -> None:
        self._depth -= 1
        if not self._fresh_line:
            self._emit_nl()
        self._fresh_line = False
        self._buf += bracket
        self._needs_comma = True

    # -- public API --

    def object_start(self) -> None:
        if self._indent > 0:
            self._pretty_open("{")
            return
        if self._needs_comma:
            self._buf += ", "
        self._buf += "{"
        self._needs_comma = False

    def object_end(self) -> None:
        if self._indent > 0:
            self._pretty_close("}")
            return
        self._buf += "}"
        self._needs_comma = True

    def array_start(self) -> None:
        if self._indent > 0:
            self._pretty_open("[")
            return
        if self._needs_comma:
            self._buf += ", "
        self._buf += "["
        self._needs_comma = False

    def array_end(self) -> None:
        if self._indent > 0:
            self._pretty_close("]")
            return
        self._buf += "]"
        self._needs_comma = True

    def key(self, k: str) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += "\""
        self._write_escaped(k)
        self._buf += "\": "
        self._needs_comma = False

    def write_str(self, v: str) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += "\""
        self._write_escaped(v)
        self._buf += "\""
        self._needs_comma = True

    def write_int(self, v: Int64) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += str(v)
        self._needs_comma = True

    def write_int32(self, v: Int32) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += str(v)
        self._needs_comma = True

    def write_bigint(self, v: int) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += str(v)
        self._needs_comma = True

    def write_float(self, v: Float64) -> None:
        # TODO: nan/inf produce invalid JSON ("nan", "inf"). JSON has no
        # special float values; CPython raises ValueError (or with
        # allow_nan=True emits NaN/Infinity). Validate or escape.
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += str(v)
        self._needs_comma = True

    def write_float32(self, v: Float32) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += str(v)
        self._needs_comma = True

    def write_bool(self, v: bool) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        if v:
            self._buf += "true"
        else:
            self._buf += "false"
        self._needs_comma = True

    def write_null(self) -> None:
        if self._indent > 0:
            self._pretty_sep()
        elif self._needs_comma:
            self._buf += ", "
        self._buf += "null"
        self._needs_comma = True

    def finish(self) -> String:
        return self._buf

    def _write_escaped(self, s: str) -> None:
        start: Int32 = 0
        i: Int32 = 0
        slen = len(s)
        while i < slen:
            c = s[i]
            esc = ""
            if c == "\"":
                esc = "\\\""
            elif c == "\\":
                esc = "\\\\"
            elif c == "\n":
                esc = "\\n"
            elif c == "\r":
                esc = "\\r"
            elif c == "\t":
                esc = "\\t"
            elif ord(c) < 32:
                # Control chars: \b, \f, and generic \u00XX
                if c == chr(8):
                    esc = "\\b"
                elif c == chr(12):
                    esc = "\\f"
                else:
                    esc = "\\u00" + _hex_byte(ord(c))
            if len(esc) > 0:
                if i > start:
                    self._buf += s[start:i]
                self._buf += esc
                start = i + 1
            i += 1
        if start < slen:
            self._buf += s[start:slen]
