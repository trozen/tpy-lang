# JSON writer: builds a JSON string incrementally.
from tpy import Int32, Int64, Float64, Float32, Char

_HEX = "0123456789abcdef"

def _hex_byte(v: Int32) -> str:
    return _HEX[v // 16] + _HEX[v % 16]

class JsonWriter:
    _parts: list[str]
    _needs_comma: bool

    def __init__(self) -> None:
        self._parts = []
        self._needs_comma = False

    def _comma(self) -> None:
        if self._needs_comma:
            self._parts.append(", ")
        self._needs_comma = False

    def object_start(self) -> None:
        self._comma()
        self._parts.append("{")
        self._needs_comma = False

    def object_end(self) -> None:
        self._parts.append("}")
        self._needs_comma = True

    def array_start(self) -> None:
        self._comma()
        self._parts.append("[")
        self._needs_comma = False

    def array_end(self) -> None:
        self._parts.append("]")
        self._needs_comma = True

    def key(self, k: str) -> None:
        self._comma()
        self._parts.append("\"")
        self._write_escaped(k)
        self._parts.append("\": ")
        self._needs_comma = False

    def write_str(self, v: str) -> None:
        self._comma()
        self._parts.append("\"")
        self._write_escaped(v)
        self._parts.append("\"")
        self._needs_comma = True

    def write_int(self, v: Int64) -> None:
        self._comma()
        self._parts.append(str(v))
        self._needs_comma = True

    def write_int32(self, v: Int32) -> None:
        self._comma()
        self._parts.append(str(v))
        self._needs_comma = True

    def write_float(self, v: Float64) -> None:
        # NOTE: nan/inf produce invalid JSON ("nan", "inf"). JSON has no
        # special float values. Callers should validate before writing.
        self._comma()
        self._parts.append(str(v))
        self._needs_comma = True

    def write_float32(self, v: Float32) -> None:
        self._comma()
        self._parts.append(str(v))
        self._needs_comma = True

    def write_bool(self, v: bool) -> None:
        self._comma()
        if v:
            self._parts.append("true")
        else:
            self._parts.append("false")
        self._needs_comma = True

    def write_null(self) -> None:
        self._comma()
        self._parts.append("null")
        self._needs_comma = True

    def finish(self) -> str:
        return "".join(self._parts)

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
                    self._parts.append(s[start:i])
                self._parts.append(esc)
                start = i + 1
            i += 1
        if start < slen:
            self._parts.append(s[start:slen])
