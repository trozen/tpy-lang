# Zero-width matches advance by a full UTF-8 character (not one byte), so
# finditer/findall/sub/split no longer panic on non-ASCII and split keeps
# the inter-match text. All results are CPython-byte-identical.
import re


def main() -> None:
    p = re.compile("x*")

    # Zero-width split keeps the characters between match positions.
    print(re.split("x*", "abc"))
    print(re.split("x*", "aé"))
    # Multibyte separator and maxsplit on a zero-width pattern.
    print(re.split("é", "aébé"))
    print(re.split("x*", "abc", maxsplit=2))
    print(re.split(",", ""))

    # Empty-match sub on multibyte input.
    print(re.sub("x*", "-", "aé", count=5))
    print(re.sub("x*", "-", "héllo", count=3))

    # finditer / findall enumerate every zero-width position on multibyte.
    print(p.findall("aé"))
    n = 0
    for _ in p.finditer("aé"):
        n += 1
    print(n)

    # A real (non-empty) multibyte match still works.
    print(re.findall("é+", "aéébé"))


main()
