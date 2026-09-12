# CPython-compatible csv module (pure TPy) over the io Readable/Writable
# protocols. Provides reader / writer (list[str] rows) and DictReader /
# DictWriter (dict[str, str] rows) with the excel default dialect plus
# delimiter/quotechar/doublequote/skipinitialspace/lineterminator kwargs; the
# writer is QUOTE_MINIMAL (a field is quoted only if it contains the delimiter,
# quotechar, CR, or LF).
#
# reader/writer/DictReader/DictWriter all borrow fp (a `Ptr` to the concrete
# file type) rather than owning it: file objects are @nocopy, and CPython's csv
# objects don't own the file either. So they must not outlive fp.
#
# DictWriter matches CPython's write-side defaults: a missing field is written
# as `restval` (default ""), and a key not in fieldnames raises ValueError
# (extrasaction='raise'). DictReader read-side divergences, both structural
# (dict[str, str] can't hold None or a list): a short row pads missing fields
# with "" (CPython's restval defaults to None); a long row silently drops the
# trailing extra cells (CPython collects them under restkey).
#
# Not yet supported: escapechar, the quoting constants, Dialect objects /
# register_dialect, Sniffer, DictReader restval/restkey, DictWriter
# extrasaction='ignore'.
#
# tpy: cpp_namespace("tpystd::csv")
from typing import Iterator
from tpy import Own, Ptr, Readable, Writable, int32


def _parse_rows[R: Readable](
    fp: Ptr[R],
    delimiter: str,
    quotechar: str,
    doublequote: bool,
    skipinitialspace: bool,
) -> Iterator[Own[list[str]]]:
    # A `Ptr[R]` (concrete) param -- not a `Readable` protocol param -- so this
    # generator can be embedded in another resumable frame (DictReader.__iter__)
    # and a borrowed-Ptr holder can drive it without a copy.
    while True:
        line = fp.readline()
        if not line:
            return
        row: list[str] = []
        parts: list[str] = []
        i: int32 = 0
        n: int32 = len(line)
        in_quotes = False
        at_field_start = True
        ended = False
        blank = False
        while not ended:
            if i >= n:
                if in_quotes:
                    # Quoted field continues on the next physical line. The
                    # embedded newline is already in `parts` -- readline keeps
                    # the trailing "\n" -- so just pull the continuation.
                    line = fp.readline()
                    if not line:
                        ended = True
                        continue
                    i = 0
                    n = len(line)
                    continue
                ended = True
                continue
            c = line[i:i + 1]
            if in_quotes:
                if c == quotechar:
                    if doublequote and i + 1 < n and line[i + 1:i + 2] == quotechar:
                        parts.append(quotechar)
                        i += 2
                        continue
                    in_quotes = False
                    i += 1
                    continue
                parts.append(c)
                i += 1
                continue
            if c == quotechar and at_field_start:
                in_quotes = True
                at_field_start = False
                i += 1
                continue
            if c == delimiter:
                row.append("".join(parts))
                parts = []
                at_field_start = True
                i += 1
                continue
            if c == "\n" or c == "\r":
                # Unquoted line terminator ends the record; the trailing
                # "\r\n" or "\n" is consumed by stopping here. A line that is
                # nothing but the terminator is a blank line -> empty row [],
                # matching CPython (which yields [] not ['']).
                if at_field_start and len(row) == 0 and len(parts) == 0:
                    blank = True
                ended = True
                continue
            if skipinitialspace and at_field_start and c == " ":
                i += 1
                continue
            at_field_start = False
            parts.append(c)
            i += 1
        if not blank:
            row.append("".join(parts))
        yield row


def reader(
    fp: Readable,
    *,
    delimiter: str = ",",
    quotechar: str = '"',
    doublequote: bool = True,
    skipinitialspace: bool = False,
) -> Iterator[Own[list[str]]]:
    # This duplicates _parse_rows' state machine, unavoidably: reader takes a
    # `Readable` protocol param (not generic `[R]`) because a cross-module call
    # `csv.reader(io.StringIO(...))` must accept an rvalue temporary, which a
    # generic `R&` param rejects; _parse_rows takes `Ptr[R]` because DictReader
    # drives it from a borrowed-Ptr field inside a resumable frame. Neither
    # shape can feed the other (a protocol value can't form a Ptr; a Ptr-param
    # generator can't take a protocol). Tracked in TODO.md.
    while True:
        line = fp.readline()
        if not line:
            return
        row: list[str] = []
        parts: list[str] = []
        i: int32 = 0
        n: int32 = len(line)
        in_quotes = False
        at_field_start = True
        ended = False
        blank = False
        while not ended:
            if i >= n:
                if in_quotes:
                    line = fp.readline()
                    if not line:
                        ended = True
                        continue
                    i = 0
                    n = len(line)
                    continue
                ended = True
                continue
            c = line[i:i + 1]
            if in_quotes:
                if c == quotechar:
                    if doublequote and i + 1 < n and line[i + 1:i + 2] == quotechar:
                        parts.append(quotechar)
                        i += 2
                        continue
                    in_quotes = False
                    i += 1
                    continue
                parts.append(c)
                i += 1
                continue
            if c == quotechar and at_field_start:
                in_quotes = True
                at_field_start = False
                i += 1
                continue
            if c == delimiter:
                row.append("".join(parts))
                parts = []
                at_field_start = True
                i += 1
                continue
            if c == "\n" or c == "\r":
                if at_field_start and len(row) == 0 and len(parts) == 0:
                    blank = True
                ended = True
                continue
            if skipinitialspace and at_field_start and c == " ":
                i += 1
                continue
            at_field_start = False
            parts.append(c)
            i += 1
        if not blank:
            row.append("".join(parts))
        yield row


def _needs_quoting(field: str, delimiter: str, quotechar: str) -> bool:
    return (
        delimiter in field
        or quotechar in field
        or "\n" in field
        or "\r" in field
    )


def _format_row(
    row: list[str],
    delimiter: str,
    quotechar: str,
    doublequote: bool,
    lineterminator: str,
) -> str:
    cells: list[str] = []
    for field in row:
        if _needs_quoting(field, delimiter, quotechar):
            inner = field.replace(quotechar, quotechar + quotechar) if doublequote else field
            cells.append(quotechar + inner + quotechar)
        else:
            cells.append(field)
    return delimiter.join(cells) + lineterminator


class _Writer[W: Writable]:
    _fp: Ptr[W]
    _delimiter: str
    _quotechar: str
    _doublequote: bool
    _lineterminator: str

    def __init__(
        self,
        fp: W,
        delimiter: str,
        quotechar: str,
        doublequote: bool,
        lineterminator: str,
    ) -> None:
        self._fp = fp
        self._delimiter = delimiter
        self._quotechar = quotechar
        self._doublequote = doublequote
        self._lineterminator = lineterminator

    def writerow(self, row: list[str]) -> None:
        self._fp.write(_format_row(
            row, self._delimiter, self._quotechar,
            self._doublequote, self._lineterminator))

    def writerows(self, rows: list[list[str]]) -> None:
        for row in rows:
            self.writerow(row)


def writer[W: Writable](
    fp: W,
    *,
    delimiter: str = ",",
    quotechar: str = '"',
    doublequote: bool = True,
    lineterminator: str = "\r\n",
) -> Own[_Writer[W]]:
    return _Writer(fp, delimiter, quotechar, doublequote, lineterminator)


class DictReader[W: Readable]:
    _fp: Ptr[W]
    _delimiter: str
    _quotechar: str
    _doublequote: bool
    _skipinitialspace: bool
    fieldnames: list[str]

    def __init__(
        self,
        fp: W,
        fieldnames: Own[list[str]] | None = None,
        *,
        delimiter: str = ",",
        quotechar: str = '"',
        doublequote: bool = True,
        skipinitialspace: bool = False,
    ) -> None:
        self._fp = fp
        self._delimiter = delimiter
        self._quotechar = quotechar
        self._doublequote = doublequote
        self._skipinitialspace = skipinitialspace
        # Initialize unconditionally (empty means "derive from header") so the
        # field is seen as set before the ctor body; override if given.
        self.fieldnames = []
        if fieldnames is not None:
            self.fieldnames = fieldnames

    def __iter__(self) -> Iterator[Own[dict[str, str]]]:
        first = True
        for row in _parse_rows(
            self._fp, self._delimiter, self._quotechar,
            self._doublequote, self._skipinitialspace,
        ):
            if first and len(self.fieldnames) == 0:
                # The first row supplies the field names when none were given.
                # Index loop, not `for name in row` -- the for-loop var over a
                # list in this resumable frame trips a Pending crash (BUGS.md).
                h: int32 = 0
                hn: int32 = len(row)
                while h < hn:
                    self.fieldnames.append(row[h])
                    h += 1
                first = False
                continue
            first = False
            out: dict[str, str] = {}
            # Index loop (not `for name in self.fieldnames`): a for-loop var
            # over a list field in this resumable frame trips a Pending-type
            # crash (BUGS.md). A short row pads missing fields with "".
            i: int32 = 0
            m: int32 = len(self.fieldnames)
            while i < m:
                name = self.fieldnames[i]
                out[name] = row[i] if i < len(row) else ""
                i += 1
            yield out


class DictWriter[W: Writable]:
    _w: _Writer[W]
    fieldnames: list[str]
    _restval: str

    def __init__(
        self,
        fp: W,
        fieldnames: Own[list[str]],
        *,
        restval: str = "",
        delimiter: str = ",",
        quotechar: str = '"',
        doublequote: bool = True,
        lineterminator: str = "\r\n",
    ) -> None:
        self._w = _Writer(fp, delimiter, quotechar, doublequote, lineterminator)
        self.fieldnames = fieldnames
        self._restval = restval

    def writeheader(self) -> None:
        self._w.writerow(self.fieldnames)

    def writerow(self, row: dict[str, str]) -> None:
        # CPython's DictWriter defaults: extrasaction='raise' (a key not in
        # fieldnames is a ValueError) and restval='' (a missing field is
        # written as restval). extrasaction='ignore' is not configurable yet.
        #
        # Index loop, not `for name in self.fieldnames`: the for-loop var binds
        # the element as a string_view, which dict.__contains__/get reject (they
        # take const str&); the subscript `self.fieldnames[i]` binds a str&.
        ordered: list[str] = []
        matched: int32 = 0
        i: int32 = 0
        m: int32 = len(self.fieldnames)
        while i < m:
            name = self.fieldnames[i]
            if name in row:
                matched += 1
            ordered.append(row.get(name, self._restval))
            i += 1
        # row carries a key not in fieldnames (assuming unique fieldnames).
        if matched < len(row):
            raise ValueError("dict contains fields not in fieldnames")
        self._w.writerow(ordered)

    def writerows(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            self.writerow(row)
