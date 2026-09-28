# JSON pull-parser: JsonToken enum and JsonReader class.
#
# TODO(perf): _read_raw_str() scans the string twice (once for escape detection,
#   once in _unescape); merge into a single pass
# TODO(perf): redundant _skip_ws() calls -- every read_* method calls it, even
#   when whitespace was already consumed by the previous call
from enum import Enum
from tpy import int32, int64, float64, char, StrView, readonly, error_return, ReturnException


class JsonError(Exception, ReturnException):
    pos: int32

    def __init__(self, message: str = "", pos: int32 = -1) -> None:
        super().__init__(message)
        self.pos = pos
        # CPython keeps the message only in `args`; `describe()` reads the attribute.
        self.message = message

    def describe(self, data: str) -> str:
        """Format error with context from the input string.

        Returns e.g.: ..."key",^---<expected ':'> 123}...
        """
        msg = self.message
        if self.pos < 0:
            if len(msg) > 0:
                return msg
            return "json error"
        ctx: int32 = 20
        dlen = len(data)
        # before
        bstart = self.pos - ctx
        prefix = ""
        if bstart < 0:
            bstart = 0
        else:
            prefix = "..."
        before = data[bstart:self.pos]
        # after
        aend = self.pos + ctx
        suffix = ""
        if aend > dlen:
            aend = dlen
        else:
            suffix = "..."
        after = data[self.pos:aend]
        if len(msg) == 0:
            msg = "error"
        gap = " " if len(after) > 0 else ""
        return prefix + before + "^---<" + msg + ">" + gap + after + suffix

class JsonToken(Enum):
    OBJECT_START = 0
    OBJECT_END = 1
    ARRAY_START = 2
    ARRAY_END = 3
    STRING = 4
    NUMBER = 5
    TRUE = 6
    FALSE = 7
    NONE = 8
    END = 9

class JsonReader:
    _data: StrView
    _pos: int32
    _len: int32

    def __init__(self, data: str) -> None:
        self._data = data
        self._pos = 0
        self._len = len(data)

    def position(self) -> int32:
        """Current byte offset into the input. Used for error reporting."""
        return self._pos

    def _skip_ws(self) -> None:
        while self._pos < self._len:
            c = self._data[self._pos]
            if c != " " and c != "\t" and c != "\n" and c != "\r":
                return
            self._pos += 1

    def peek(self) -> JsonToken:
        self._skip_ws()
        if self._pos >= self._len:
            return JsonToken.END
        c = self._data[self._pos]
        if c == "{":
            return JsonToken.OBJECT_START
        if c == "}":
            return JsonToken.OBJECT_END
        if c == "[":
            return JsonToken.ARRAY_START
        if c == "]":
            return JsonToken.ARRAY_END
        if c == "\"":
            return JsonToken.STRING
        if c == "t":
            return JsonToken.TRUE
        if c == "f":
            return JsonToken.FALSE
        if c == "n":
            return JsonToken.NONE
        return JsonToken.NUMBER

    @error_return(JsonError)
    def read_object_start(self) -> None:
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "{"):
            raise JsonError("expected '{'", self._pos)
        self._pos += 1

    @error_return(JsonError)
    def read_object_end(self) -> None:
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "}"):
            raise JsonError("expected '}'", self._pos)
        self._pos += 1

    @error_return(JsonError)
    def read_array_start(self) -> None:
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "["):
            raise JsonError("expected '['", self._pos)
        self._pos += 1

    @error_return(JsonError)
    def read_array_end(self) -> None:
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "]"):
            raise JsonError("expected ']'", self._pos)
        self._pos += 1

    def has_next(self) -> bool:
        self._skip_ws()
        if self._pos >= self._len:
            return False
        c = self._data[self._pos]
        if c == "}" or c == "]":
            return False
        if c == ",":
            self._pos += 1
            self._skip_ws()
        return True

    @error_return(JsonError)
    def read_key(self) -> str:
        result = self._read_raw_str()
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == ":"):
            raise JsonError("expected ':'", self._pos)
        self._pos += 1
        return result

    @error_return(JsonError)
    def read_key_raw(self) -> StrView:
        """Read an object key without processing escape sequences.

        Returns a view into the input data. Faster than read_key()
        for keys that are plain identifiers (no allocation).
        """
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "\""):
            raise JsonError("expected '\"'", self._pos)
        self._pos += 1
        start = self._pos
        while self._pos < self._len and self._data[self._pos] != "\"":
            self._pos += 1
        if not (self._pos < self._len):
            raise JsonError("unterminated string", start - 1)
        end = self._pos
        self._pos += 1
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == ":"):
            raise JsonError("expected ':'", self._pos)
        self._pos += 1
        return self._data[start:end]

    @error_return(JsonError)
    def read_str(self) -> str:
        return self._read_raw_str()

    @error_return(JsonError)
    def read_str_raw(self) -> StrView:
        """Read a string value without processing escape sequences.

        Returns a view into the input data. Use for values known to
        be plain text (no allocation).
        """
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "\""):
            raise JsonError("expected '\"'", self._pos)
        self._pos += 1
        start = self._pos
        while self._pos < self._len and self._data[self._pos] != "\"":
            self._pos += 1
        if not (self._pos < self._len):
            raise JsonError("unterminated string", start - 1)
        end = self._pos
        self._pos += 1
        return self._data[start:end]

    @error_return(JsonError)
    def read_int(self) -> int64:
        self._skip_ws()
        neg = False
        if self._pos < self._len and self._data[self._pos] == "-":
            neg = True
            self._pos += 1
        result: int64 = 0
        start = self._pos
        while self._pos < self._len:
            c = self._data[self._pos]
            if c < "0" or c > "9":
                break
            result = result * 10 + int64(ord(c) - ord("0"))
            self._pos += 1
        if not (self._pos > start):
            raise JsonError("expected digit", self._pos)
        if neg:
            result = -result
        return result

    @error_return(JsonError)
    def read_float(self) -> float64:
        raw: StrView = self._read_number_raw()
        return float(raw)

    @error_return(JsonError)
    def read_number_raw(self) -> StrView:
        """Read the raw text of a JSON number without parsing it.

        Returns a view into the input. Caller decides int vs float (e.g. by
        scanning for '.', 'e', 'E') and parses accordingly. Used by the
        stdlib `json` wrapper to preserve BigInt precision.
        """
        return self._read_number_raw()

    @error_return(JsonError)
    def read_bool(self) -> bool:
        self._skip_ws()
        if self._pos + 4 <= self._len and self._data[self._pos:self._pos + 4] == "true":
            self._pos += 4
            return True
        if self._pos + 5 <= self._len and self._data[self._pos:self._pos + 5] == "false":
            self._pos += 5
            return False
        raise JsonError("expected 'true' or 'false'", self._pos)

    @error_return(JsonError)
    def read_null(self) -> None:
        self._skip_ws()
        if self._pos + 4 <= self._len and self._data[self._pos:self._pos + 4] == "null":
            self._pos += 4
            return
        raise JsonError("expected 'null'", self._pos)

    @error_return(JsonError)
    def skip_value(self) -> None:
        self._skip_ws()
        if self._pos >= self._len:
            return
        c = self._data[self._pos]
        if c == "\"":
            self._skip_str_no_ws()
        elif c == "{":
            self._pos += 1
            while self.has_next():
                self._skip_key()
                self.skip_value()
            self._pos += 1
        elif c == "[":
            self._pos += 1
            while self.has_next():
                self.skip_value()
            self._pos += 1
        elif c == "t":
            self._pos += 4
        elif c == "f":
            self._pos += 5
        elif c == "n":
            self._pos += 4
        else:
            self._skip_number()

    # -- internal helpers --

    @error_return(JsonError)
    def _skip_str(self) -> None:
        """Advance past a JSON string without allocating. Handles escapes."""
        self._skip_ws()
        self._skip_str_no_ws()

    def _skip_str_no_ws(self) -> None:
        """Advance past a JSON string, assuming _pos is on the opening quote."""
        self._pos += 1
        while self._pos < self._len:
            c = self._data[self._pos]
            if c == "\\":
                self._pos += 2
            elif c == "\"":
                self._pos += 1
                return
            else:
                self._pos += 1

    @error_return(JsonError)
    def _skip_key(self) -> None:
        """Skip an object key and its trailing colon. No allocation."""
        self._skip_str_no_ws()
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == ":"):
            raise JsonError("expected ':'", self._pos)
        self._pos += 1

    def _skip_number(self) -> None:
        """Advance past a JSON number. No allocation, no return value."""
        if self._pos < self._len and self._data[self._pos] == "-":
            self._pos += 1
        while self._pos < self._len:
            c = self._data[self._pos]
            if c >= "0" and c <= "9":
                self._pos += 1
            elif c == ".":
                self._pos += 1
            elif c == "e" or c == "E":
                self._pos += 1
                if self._pos < self._len and (self._data[self._pos] == "+" or self._data[self._pos] == "-"):
                    self._pos += 1
            else:
                return

    @error_return(JsonError)
    def _read_raw_str(self) -> str:
        self._skip_ws()
        if not (self._pos < self._len and self._data[self._pos] == "\""):
            raise JsonError("expected '\"'", self._pos)
        self._pos += 1
        start = self._pos
        has_escape = False
        while self._pos < self._len:
            c = self._data[self._pos]
            if c == "\\":
                has_escape = True
                self._pos += 2
            elif c == "\"":
                break
            else:
                self._pos += 1
        if not (self._pos < self._len):
            raise JsonError("unterminated string", start - 1)
        end = self._pos
        self._pos += 1
        if not has_escape:
            return self._data[start:end]
        return self._unescape(start, end)

    def _unescape(self, start: int32, end: int32) -> str:
        result = ""
        i = start
        chunk_start = start
        while i < end:
            if self._data[i] == "\\":
                if i > chunk_start:
                    result = result + self._data[chunk_start:i]
                i += 1
                esc = self._data[i]
                if esc == "\"":
                    result = result + "\""
                elif esc == "\\":
                    result = result + "\\"
                elif esc == "/":
                    result = result + "/"
                elif esc == "n":
                    result = result + "\n"
                elif esc == "r":
                    result = result + "\r"
                elif esc == "t":
                    result = result + "\t"
                elif esc == "b":
                    result = result + chr(8)
                elif esc == "f":
                    result = result + chr(12)
                elif esc == "u":
                    # \uXXXX: parse 4 hex digits
                    if not (i + 4 < end):
                        # Incomplete escape -- just skip
                        i += 1
                        chunk_start = i
                        continue
                    code = self._parse_hex4(i + 1)
                    # TODO: chr(code) truncates to 1 byte; codepoints > 0xFF
                    # silently lose their high bits. Need a chr that emits
                    # multi-byte UTF-8 for the BMP, plus surrogate-pair
                    # handling for codepoints > 0xFFFF.
                    result = result + chr(code)
                    i += 4
                else:
                    result = result + "\\"
                    result = result + esc
                i += 1
                chunk_start = i
            else:
                i += 1
        if chunk_start < end:
            result = result + self._data[chunk_start:end]
        return result

    def _parse_hex4(self, pos: int32) -> int32:
        # TODO: invalid hex digits silently contribute 0 instead of raising
        # JsonError. CPython rejects with `Invalid \uXXXX escape`.
        result: int32 = 0
        i: int32 = 0
        while i < 4:
            c = self._data[pos + i]
            if c >= "0" and c <= "9":
                result = result * 16 + (ord(c) - ord("0"))
            elif c >= "a" and c <= "f":
                result = result * 16 + (ord(c) - ord("a") + 10)
            elif c >= "A" and c <= "F":
                result = result * 16 + (ord(c) - ord("A") + 10)
            else:
                result = result * 16
            i += 1
        return result

    # TODO: shape validation is loose -- accepts malformed numbers like
    # `1.`, `1e`, `1..2`, and leading zeros (`01`) that CPython rejects.
    # Tighten when the BigInt/float parsers stop being lenient.
    @error_return(JsonError)
    def _read_number_raw(self) -> StrView:
        self._skip_ws()
        start = self._pos
        # Optional leading minus
        if self._pos < self._len and self._data[self._pos] == "-":
            self._pos += 1
        # Must have at least one leading digit
        if not (self._pos < self._len and self._data[self._pos] >= "0" and self._data[self._pos] <= "9"):
            raise JsonError("expected number", start)
        # Digits, decimal point, exponent
        while self._pos < self._len:
            c = self._data[self._pos]
            if c >= "0" and c <= "9":
                self._pos += 1
            elif c == ".":
                self._pos += 1
            elif c == "e" or c == "E":
                self._pos += 1
                # Optional +/- after exponent
                if self._pos < self._len and (self._data[self._pos] == "+" or self._data[self._pos] == "-"):
                    self._pos += 1
            else:
                break
        return self._data[start:self._pos]
