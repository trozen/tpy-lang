# csv.reader / csv.writer (list[str] row surface) over io text buffers --
# parity-checked against real CPython csv (the cpy phase).
import csv
import io


def read_basic() -> None:
    data = "a,b,c\n1,2,3\nx,y,z\n"
    for row in csv.reader(io.StringIO(data)):
        print(len(row), "|".join(row))


def read_quoted() -> None:
    # Quoted field with an embedded delimiter, a doubled quote, and an
    # embedded newline that spans physical lines.
    data = '1,"two, comma",3\r\n"q ""x""","ln\nbrk",z\r\n'
    for row in csv.reader(io.StringIO(data)):
        print(len(row), "::".join(row))


def read_custom_delim() -> None:
    for row in csv.reader(io.StringIO("a;b;c\n"), delimiter=";"):
        print("|".join(row))


def read_skipinitialspace() -> None:
    for row in csv.reader(io.StringIO("a,  b,   c\n"), skipinitialspace=True):
        print("|".join(row))


def read_empty_fields() -> None:
    for row in csv.reader(io.StringIO("a,,c\n,,\n")):
        print(len(row), "[" + "|".join(row) + "]")


def read_blank_and_empty() -> None:
    # A blank line is an empty row [] (not ['']); a fully empty input yields
    # no rows at all. Both match CPython.
    for row in csv.reader(io.StringIO("a\n\nb\n")):
        print(len(row), "[" + "|".join(row) + "]")
    count = 0
    for row in csv.reader(io.StringIO("")):
        count += 1
    print("empty-input rows:", count)


def read_then_mutate() -> None:
    # The yielded row is a fresh, independently-owned list[str] (a reference
    # type): mutating it after the yield is allowed and local, matching
    # CPython (which also yields a fresh list per row).
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO("a,b\nc,d\n")):
        row.append("EXTRA")
        rows.append(row)
    print(rows[0])
    print(rows[1])


def write_basic() -> None:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["a", "b", "c"])
    w.writerows([["1", "2", "3"], ["x", "y", "z"]])
    print(repr(out.getvalue()))


def write_quoting() -> None:
    out = io.StringIO()
    w = csv.writer(out)
    # QUOTE_MINIMAL: quote only fields containing delimiter / quote / CR / LF.
    w.writerow(["plain", "has,comma", 'has"quote', "embed\nnl", "embed\rcr"])
    print(repr(out.getvalue()))


def write_custom() -> None:
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    w.writerow(["a", "b"])
    w.writerow(["c", "d"])
    print(repr(out.getvalue()))


def roundtrip() -> None:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["name", "note"])
    w.writerow(["Alice", "says, hi"])
    text = out.getvalue()
    for row in csv.reader(io.StringIO(text)):
        print("|".join(row))


def main() -> None:
    read_basic()
    print("---")
    read_quoted()
    print("---")
    read_custom_delim()
    print("---")
    read_skipinitialspace()
    print("---")
    read_empty_fields()
    print("---")
    read_blank_and_empty()
    print("---")
    read_then_mutate()
    print("---")
    write_basic()
    print("---")
    write_quoting()
    print("---")
    write_custom()
    print("---")
    roundtrip()


main()
