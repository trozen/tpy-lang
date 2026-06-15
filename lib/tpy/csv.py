# CPython-compatible csv module (pure TPy) over the io Readable/Writable
# protocols. Provides reader / writer for the list[str] row surface with the
# excel default dialect plus delimiter/quotechar/doublequote/skipinitialspace/
# lineterminator kwargs; the writer is QUOTE_MINIMAL (a field is quoted only if
# it contains the delimiter, quotechar, CR, or LF).
#
# The writer borrows fp (a `Ptr[W]` field) rather than owning it: file objects
# are @nocopy, and CPython's csv writer doesn't own the file either. So the
# writer must not outlive fp.
#
# Not yet supported: DictReader / DictWriter, escapechar, the quoting
# constants, Dialect objects / register_dialect, Sniffer.
#
# tpy: cpp_namespace("tpystd::csv")
from typing import Iterator
from tpy import Own, Ptr, Readable, Writable, Int32


def reader(
    fp: Readable,
    *,
    delimiter: str = ",",
    quotechar: str = '"',
    doublequote: bool = True,
    skipinitialspace: bool = False,
) -> Iterator[Own[list[str]]]:
    while True:
        line = fp.readline()
        if not line:
            return
        row: list[str] = []
        parts: list[str] = []
        i: Int32 = 0
        n: Int32 = len(line)
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
