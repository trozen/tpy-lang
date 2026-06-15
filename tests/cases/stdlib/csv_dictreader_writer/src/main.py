# csv.DictReader / csv.DictWriter (dict[str, str] row surface) over io text
# buffers -- parity-checked against real CPython csv (the cpy phase).
import io
# Direct import: module-qualified construction of a generic class
# (`csv.DictReader(...)`) is a known compiler gap (BUGS.md).
from csv import DictReader, DictWriter


def read_header_derived() -> None:
    data = "name,age,city\nAlice,30,NYC\nBob,25,LA\n"
    for row in DictReader(io.StringIO(data)):
        print(row["name"], row["age"], row["city"])


def read_explicit_fieldnames() -> None:
    # No header row in the data; fieldnames supplied explicitly. (Bound to a
    # local: a list literal doesn't coerce to the `list[str] | None` param --
    # a known literal-into-Optional-param gap, see BUGS.md.)
    data = "Alice,30\nBob,25\n"
    fn: list[str] = ["name", "age"]
    for row in DictReader(io.StringIO(data), fn):
        print(row["name"], row["age"])


def read_quoted() -> None:
    data = 'name,note\nAlice,"says, hi"\nBob,"line\nbreak"\n'
    for row in DictReader(io.StringIO(data)):
        print(row["name"], "::", row["note"])


def read_short_row() -> None:
    # A short row is padded to full width (matches CPython structurally).
    # Only the missing field's value diverges -- "" here vs CPython's
    # restval=None -- so this checks len + key presence + present fields, not
    # the missing value.
    data = "a,b,c\n1,2\n"
    for row in DictReader(io.StringIO(data)):
        print(len(row), "c" in row, row["a"], row["b"])


def read_long_row() -> None:
    # A row with more fields than the header drops the trailing extras (CPython
    # collects them under a None restkey, so its len would differ -- only the
    # named fields are compared here, which match).
    data = "a,b\n1,2,3,4\n"
    for row in DictReader(io.StringIO(data)):
        print(row["a"], row["b"])


def write_basic() -> None:
    out = io.StringIO()
    w = DictWriter(out, ["name", "age"])
    w.writeheader()
    w.writerow({"name": "Alice", "age": "30"})
    w.writerows([{"name": "Bob", "age": "25"}, {"name": "Cy", "age": "40"}])
    print(repr(out.getvalue()))


def write_quoting() -> None:
    out = io.StringIO()
    w = DictWriter(out, ["k", "v"])
    w.writeheader()
    w.writerow({"k": "x", "v": "has,comma"})
    print(repr(out.getvalue()))


def write_missing_key() -> None:
    # A missing field is written as restval (default ""), matching CPython.
    out = io.StringIO()
    w = DictWriter(out, ["name", "age"])
    w.writerow({"name": "Alice"})
    print(repr(out.getvalue()))


def write_extra_key_raises() -> None:
    # A key not in fieldnames raises ValueError (CPython extrasaction='raise').
    out = io.StringIO()
    w = DictWriter(out, ["name"])
    try:
        w.writerow({"name": "Alice", "extra": "x"})
        print("FAIL: no ValueError")
    except ValueError:
        print("got ValueError")


def roundtrip() -> None:
    out = io.StringIO()
    w = DictWriter(out, ["name", "note"])
    w.writeheader()
    w.writerow({"name": "Alice", "note": "says, hi"})
    for row in DictReader(io.StringIO(out.getvalue())):
        print(row["name"], "->", row["note"])


def main() -> None:
    read_header_derived()
    print("---")
    read_explicit_fieldnames()
    print("---")
    read_quoted()
    print("---")
    read_short_row()
    print("---")
    read_long_row()
    print("---")
    write_basic()
    print("---")
    write_quoting()
    print("---")
    write_missing_key()
    print("---")
    write_extra_key_raises()
    print("---")
    roundtrip()


main()
