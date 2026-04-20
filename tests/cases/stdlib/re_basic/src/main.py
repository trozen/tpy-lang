# re: CPython-compatible surface -- compile/search/match/fullmatch/findall/
# split/sub (no backref), flags, error class. This test is intentionally
# limited to the operations whose output matches CPython byte-for-byte;
# PCRE2-specific behaviors (sub with $1 backref, groups() returning list
# vs tuple) are isolated in re_pcre2_specific/.
from re import (
    compile, search, match, fullmatch, sub, split, findall, Pattern, Match,
    IGNORECASE, MULTILINE, DOTALL, VERBOSE, ASCII,
    error,
)

def main() -> None:
    # ---------- compile + Pattern repr-stable accessors ----------
    p: Pattern = compile(r"(\w+)\s+(\w+)")
    print(f"pat='{p.pattern}'")

    # ---------- search: full match + per-group access ----------
    m = p.search("hello world")
    if m is None:
        print("FAIL: search None")
        return
    print(f"g0='{m.group()}'")
    print(f"g1='{m.group(1)}'")
    print(f"g2='{m.group(2)}'")
    print(f"span={m.span()}")
    print(f"start={m.start()} end={m.end()}")
    print(f"g1.span={m.span(1)} g2.span={m.span(2)}")

    # ---------- match (anchored at start) ----------
    p_dig: Pattern = compile(r"\d+")
    print(f"match-yes={p_dig.match('123abc') is not None}")
    print(f"match-no={p_dig.match('abc123') is None}")

    # ---------- fullmatch (anchored both ends) ----------
    print(f"full-yes={p_dig.fullmatch('123') is not None}")
    print(f"full-no-tail={p_dig.fullmatch('123x') is None}")
    print(f"full-no-head={p_dig.fullmatch('x123') is None}")

    # ---------- search returning None ----------
    print(f"none={search('zzz', 'hello') is None}")

    # ---------- findall (no captures -> list of full matches in both impls) ----------
    print(f"findall-digits={p_dig.findall('a1 b22 c333')}")
    print(f"findall-words={compile(r'\w+').findall('foo bar baz')}")
    print(f"findall-empty={p_dig.findall('no digits here')}")

    # ---------- split ----------
    print(f"split-comma={compile(r',').split('a,b,c,d')}")
    print(f"split-ws={compile(r'\s+').split('one  two   three')}")
    print(f"split-max2={compile(r',').split('a,b,c,d,e', 2)}")
    print(f"split-no-match={compile(r'X').split('abc')}")
    # Leading + trailing delimiter -- both sides should produce empty strings.
    print(f"split-edges={compile(r',').split(',a,b,')}")

    # ---------- sub WITHOUT backref (literal replacement -- CPython-compat) ----------
    print(f"sub-lit={sub(r'\d+', 'NUM', 'a1 b22 c333')}")
    print(f"sub-no-match={sub(r'X', 'Y', 'abc')}")
    print(f"sub-empty={sub(r'\s+', '', 'a b  c   d')}")
    # Zero-width-match pattern -- exercises the bump-along path inside sub.
    print(f"sub-zerow={sub(r'x*', '-', 'abc')}")

    # ---------- Module-level API matches Pattern-instance API ----------
    m_mod = search(r'(\w+)', 'foo bar')
    if m_mod is not None:
        print(f"mod-search='{m_mod.group()}'")
    print(f"mod-match={match(r'\d', '5x') is not None}")
    print(f"mod-fullmatch={fullmatch(r'\d+', '42') is not None}")

    # ---------- IGNORECASE ----------
    print(f"icase-search={compile(r'hello', IGNORECASE).search('HELLO World') is not None}")
    print(f"icase-no={compile(r'hello').search('HELLO') is None}")

    # ---------- MULTILINE: ^ matches start of each line ----------
    p_ml: Pattern = compile(r"^line", MULTILINE)
    print(f"ml-count={len(p_ml.findall('line1\nline2\nfoo\nline3'))}")
    print(f"ml-off={len(compile(r'^line').findall('line1\nline2\nfoo'))}")

    # ---------- DOTALL: . matches newline ----------
    print(f"dotall-yes={compile(r'a.c', DOTALL).search('a\nc') is not None}")
    print(f"dotall-no={compile(r'a.c').search('a\nc') is None}")

    # ---------- VERBOSE: whitespace + # comments stripped from pattern ----------
    p_v: Pattern = compile(r"""
        \d+    # one or more digits
        \s+    # whitespace
        \w+    # word characters
    """, VERBOSE)
    m_v = p_v.search("123 abc")
    print(f"verbose-match={m_v is not None}")
    if m_v is not None:
        print(f"verbose-g0='{m_v.group()}'")

    # ---------- ASCII flag is accepted (CPython-compat) ----------
    # We just check that an ASCII-flag pattern matches ASCII input. Testing
    # the actual non-ASCII divergence requires Unicode source strings, which
    # we keep out of test fixtures for portability.
    m_a = compile(r'\w+', ASCII).search('hello')
    print(f"ascii-yes={m_a is not None}")

    # ---------- Pattern features: quantifiers + classes + anchors ----------
    print(f"q-star={compile(r'a*').findall('aaabaa')}")
    print(f"q-range={compile(r'\d{2,3}').findall('1 22 333 4444')}")
    print(f"alt={compile(r'cat|dog').findall('a cat and a dog')}")
    print(f"anchor-start={compile(r'^foo').findall('foo bar')}")
    print(f"anchor-end={compile(r'baz$').findall('foo bar baz')}")
    print(f"word-bound={compile(r'\bcat\b').findall('cat scatter category cat')}")

    # ---------- Backrefs IN PATTERN (\1) work in both PCRE2 and CPython ----------
    print(f"backref-pat={compile(r'(\w+) \1').search('foo foo bar') is not None}")
    print(f"backref-no={compile(r'(\w+) \1').search('foo bar') is None}")

    # ---------- Non-capturing group (?:...) ----------
    m_nc = compile(r"(?:\d+)-(\w+)").search("123-abc")
    if m_nc is not None:
        print(f"non-cap-g1='{m_nc.group(1)}'")

    # ---------- error class is catchable on bad patterns ----------
    try:
        compile("(unclosed")
        print("FAIL: should have raised")
    except error:
        # Don't print the message -- PCRE2 and CPython phrase it differently;
        # just confirm we caught the right exception type.
        print("err-caught=True")

    print("ok")

main()
