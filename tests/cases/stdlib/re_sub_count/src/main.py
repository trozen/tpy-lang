# `count` parameter on re.sub / Pattern.sub: limits replacements; 0 means
# all, negative means none (CPython behavior). Includes the empty-match
# advancement edge cases (retry non-empty at the same position, then skip
# one char) and growing/shrinking replacements.
import re


def main() -> None:
    print(re.sub(r"\d+", "N", "a1 b22 c333", count=1))
    print(re.sub(r"\d+", "N", "a1 b22 c333", count=2))
    print(re.sub(r"\d+", "N", "a1 b22 c333", count=99))
    print(re.sub(r"\d+", "N", "a1 b22 c333", count=0))
    print(re.sub(r"\d+", "N", "a1 b22 c333", count=-1))
    # Growing replacement shifts later match positions.
    print(re.sub("a", "xyz", "aaa", count=2))
    # Empty matches: replaced at each position, non-empty retried at the
    # same position first ('|a' matches empty then 'a' at offset 0).
    print(re.sub("x*", "-", "abc", count=2))
    print(re.sub("x*", "-", "xxabc", count=2))
    print(re.sub("|a", "-", "ab", count=3))
    # Pattern method form.
    p = re.compile("b+")
    print(p.sub("B", "abba bb b", 2))
    print(p.sub("B", "abba bb b"))


main()
