# re: PCRE2-specific behavior that diverges from CPython.
#
# Two known divergences (each tracked as TODO(v2) in lib/tpy/re.py):
#   1. sub backref syntax: PCRE2 uses $1 / ${name}; CPython uses \1 / \g<name>.
#      Until the facade adds a `\1`->`$1` translator, sub-with-backref output
#      diverges from CPython.
#   2. Match.groups() returns list[str] in TPy (varadic-tuple support pending),
#      vs tuple[str, ...] in CPython -- print formatting differs ([] vs ()).
#
# Marked no_cpython because the prints below would mismatch.
from re import compile, sub, Pattern, Match

def main() -> None:
    # ---------- sub with PCRE2 $1 backref ----------
    # CPython would treat $1 as literal text; PCRE2 expands to the capture.
    print(f"sub-bref-1={sub(r'(\w+)', '[$1]', 'foo bar baz')}")
    print(f"sub-bref-2={sub(r'(\w+)\s+(\w+)', '$2 $1', 'hello world')}")

    # ---------- groups() returns list[str] (vs CPython tuple) ----------
    p: Pattern = compile(r"(\d+)-(\d+)-(\d+)")
    m = p.search("date 2024-01-15 here")
    if m is None:
        print("FAIL: groups search")
        return
    print(f"groups={m.groups()}")

    # No-capture pattern: groups() empty
    m2 = compile(r"\d+").search("abc 42")
    if m2 is None:
        print("FAIL: nocap search")
        return
    print(f"nocap-groups={m2.groups()}")

    print("ok")

main()
