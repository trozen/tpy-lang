# base64: encode/decode for b64 (standard + urlsafe), b16, b32, covering
# all padding remainders and the urlsafe alphabet divergence from standard.
from base64 import (
    b64encode, b64decode,
    standard_b64encode, standard_b64decode,
    urlsafe_b64encode, urlsafe_b64decode,
    b16encode, b16decode,
    b32encode, b32decode,
    encodebytes, decodebytes,
)

def main() -> None:
    # b64: all three padding remainders + roundtrip.
    print(b64encode(b"Man"))
    print(b64decode(b"TWFu"))
    print(b64encode(b"Ma"))
    print(b64decode(b"TWE="))
    print(b64encode(b"M"))
    print(b64decode(b"TQ=="))
    print(b64encode(b""))
    print(b64decode(b""))
    print(b64encode(b"hello world"))
    print(b64decode(b"aGVsbG8gd29ybGQ="))

    # standard_b64* are aliases.
    print(standard_b64encode(b"Man"))
    print(standard_b64decode(b"TWFu"))

    # urlsafe: bytes that trigger + and / in the standard alphabet get -/_.
    raw: bytes = b"\xfb\xff"
    print(b64encode(raw))
    print(urlsafe_b64encode(raw))
    print(urlsafe_b64decode(b"-_8="))
    print(b64decode(b"+/8="))

    # b16: all-zero, simple, roundtrip.
    print(b16encode(b""))
    print(b16encode(b"\x00"))
    print(b16encode(b"Hi!"))
    print(b16decode(b"486921"))

    # b32: every input-length mod 5 triggers a different pad count.
    print(b32encode(b""))
    print(b32encode(b"f"))         # 1 -> 6 pads
    print(b32encode(b"fo"))        # 2 -> 4 pads
    print(b32encode(b"foo"))       # 3 -> 3 pads
    print(b32encode(b"foob"))      # 4 -> 1 pad
    print(b32encode(b"fooba"))     # 5 -> 0 pads
    print(b32encode(b"foobar"))    # 6 -> 6 pads (5+1)
    print(b32decode(b"MY======"))
    print(b32decode(b"MZXQ===="))
    print(b32decode(b"MZXW6==="))
    print(b32decode(b"MZXW6YQ="))
    print(b32decode(b"MZXW6YTB"))
    print(b32decode(b"MZXW6YTBOI======"))

    # altchars on b64: same alphabet swap as urlsafe_*, callable form.
    print(b64encode(raw, b"-_"))
    print(b64decode(b"-_8=", b"-_"))

    # validate=False (CPython default) skips non-alphabet chars.
    print(b64decode(b"TWFu\n"))
    print(b64decode(b"T W F u"))
    # validate=True: strict, must consist entirely of alphabet + padding.
    print(b64decode(b"TWFu", None, True))

    # b32 casefold + map01.
    print(b32decode(b"mzxw6ytb", True))
    print(b32decode(b"MZXW0YTB", False, b"I"))  # '0' -> 'O'
    print(b32decode(b"MZXW1YTB", False, b"L"))  # '1' -> 'L'

    # bytearray input: accepted via span coercion.
    ba: bytearray = bytearray(b"Man")
    print(b64encode(ba))

    # encodebytes: MIME-style 76-char line wrap + trailing newline.
    print(encodebytes(b""))
    print(encodebytes(b"hello"))
    print(encodebytes(b"a" * 76))
    print(encodebytes(b"a" * 77))
    print(decodebytes(b"YWFh\n"))
    print(decodebytes(b"aGVsbG8=\n"))

    # bytes/bytearray inputs share the code path; verify with a standard
    # bytearray through urlsafe too.
    print(urlsafe_b64encode(bytearray(b"\xfb\xff")))

    # str input on decoders (CPython accepts ASCII str; encoders don't).
    print(b64decode("TWFu"))
    print(standard_b64decode("TWFu"))
    print(urlsafe_b64decode("-_8="))
    print(b32decode("MZXW6YTB"))
    print(b32decode("mzxw6ytb", True))
    print(b16decode("486921"))
    print(b16decode("48af", True))

    # Error paths -- catchable ValueError, matching CPython's binascii.Error
    # (which subclasses ValueError). altchars/map01 length validation uses
    # AssertionError in CPython and ValueError in TPy, so those aren't covered
    # here (would break byte-identical cpy parity).
    try:
        b64decode(b"????", None, True)  # validate=True + invalid chars
    except ValueError:
        print("b64 validate raised")
    try:
        b32decode(b"MZXQ===")  # 7 chars, not a multiple of 8
    except ValueError:
        print("b32 length raised")
    try:
        b16decode(b"4G")  # G not a valid hex char
    except ValueError:
        print("b16 char raised")

main()
