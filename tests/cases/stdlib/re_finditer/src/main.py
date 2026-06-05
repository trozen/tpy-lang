# re.Pattern.finditer is a lazy generator yielding Match objects. Exercises
# the public finditer surface directly (findall only covers it internally).
import re


def main() -> None:
    p = re.compile(r"\d+")
    for m in p.finditer("a12b345c6"):
        print(m.group(0), m.start(), m.end())

    total = 0
    for m in p.finditer("xx 7 yy 88 zz 900"):
        total += int(m.group(0))
    print(total)

    # No matches -> generator yields nothing.
    n = 0
    for m in p.finditer("no digits here"):
        n += 1
    print(n)


main()
