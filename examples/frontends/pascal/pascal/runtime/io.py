"""Pascal I/O runtime: write / writeln / read / readln.

M1: only `writeln(StrView)` is implemented -- enough for hello-world.
Subsequent milestones add integer / float / boolean overloads, the
`write` (no-newline) variants, and `read` / `readln` for stdin.
"""

def writeln(s: str) -> None:
    print(s)
