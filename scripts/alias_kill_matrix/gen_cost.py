"""The compile-time cost rows of the acceptance matrix, generated per run
(`run_matrix.py` writes them under build/; they are never committed: 18k
lines of output of a deterministic generator).

Run: python gen_cost.py [out_dir]   (default: build/cost next to this file)
Verdict for a cost row = compile seconds (tpyc file.py -o out --no-bundle-runtime)
minus the tree's trivial-program baseline; expected within 2x of the baseline.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "build" / "cost"
Q = '"'
HEAD = "# GROUP: cost\n# EXPECTED: <=2x\n"


def rec(name: str, nf: int, extra: str = "") -> list[str]:
    s = [f"class {name}:", "    v: int | None"]
    s += [f"    f{i}: Ptr[{Q}{name}{Q}]" for i in range(nf)]
    s += ["", "    def __init__(self) -> None:", "        self.v = 1"]
    s += [f"        self.f{i} = None" for i in range(nf)]
    s += ["", "    def reset(self) -> None:", "        self.v = None", extra, ""]
    return s


def write(name: str, desc: str, lines: list[str]) -> None:
    (OUT / f"{name}.py").write_text(f"# {desc}\n" + HEAD + "\n".join(lines) + "\n")


def walk(nf: int) -> None:
    s = ["from tpy import Ptr", ""] + rec("R", nf)
    s += ["def g(root: R, k: int) -> None:", "    node: Ptr[R] = root", "    i = 0",
          "    while node is not None and i < 3:", "        if node.v is not None:",
          "            node.v = 2", "            print(node.v + 1)"]
    for i in range(nf):
        s += [f"        if k == {i}:", f"            node = node.f{i}"]
    s += ["        i += 1", "    if root.v is not None:", "        if node is not None:",
          "            node.reset()", "        print(root.v)", "", "", "g(R(), 0)"]
    write(f"cost_walk{nf}", f"walk over a record with {nf} pointer fields, narrowed store", s)


def field_chain(n: int) -> None:
    s = ["from tpy import Ptr", ""] + rec("R", 1)
    s += ["def g(head: R) -> None:", "    a0: Ptr[R] = head"]
    for i in range(1, n + 1):
        s += [f"    a{i}: Ptr[R] = None", f"    if a{i-1} is not None:", f"        a{i} = a{i-1}.f0"]
    s += ["    if head.v is not None:", f"        if a{n} is not None:", f"            a{n}.reset()",
          "        print(head.v)", "", "", "g(R())"]
    write(f"cost_field_chain_{n}", f"field chain a_i = a_(i-1).f0 of {n} names", s)


def alias_chain(n: int) -> None:
    s = ["class N:", "    v: int | None", "", "    def __init__(self) -> None:", "        self.v = 1", "", "",
         "def g(a: N) -> None:", "    t0 = a"]
    for i in range(1, n + 1):
        s.append(f"    t{i} = t{i-1}")
    s += ["    if a.v is not None:", f"        t{n}.v = None", "        print(a.v)", "", "", "g(N())"]
    write(f"cost_alias_chain_{n}", f"alias chain t_i = t_(i-1) of {n} names", s)


def scc(n: int) -> None:
    s = ["from tpy import Ptr", ""] + rec("R", 8)
    s += ["def g(r: R, s: R, k: int) -> None:"]
    s += [f"    x{i}: Ptr[R] = r" for i in range(n)]
    s += ["    i = 0", "    while i < 3:"]
    for i in range(n):
        p, q = (i - 1) % n, (i * 7 + 3) % n
        s += [f"        if x{p} is not None and x{q} is not None:", f"            x{i} = x{p}.f{i % 8}",
              f"        if k == {i} and x{q} is not None:", f"            x{i} = x{q}.f{(i + 3) % 8}"]
    s += ["        i += 1", "    if r.v is not None:"]
    for i in range(n):
        s += [f"        if x{i} is not None and x{i}.v is not None:", f"            x{i}.reset()",
              f"            print({Q}r{i}{Q}, r.v)"]
    s += ["", "", "g(R(), R(), 0)"]
    write(f"cost_scc_{n}", f"one strongly connected group of {n} pointer names with stores and facts", s)


def many(nc: int, nf: int) -> None:
    s = ["from tpy import Ptr", ""]
    for c in range(nc):
        s += rec(f"R{c}", nf)
    args = ", ".join(f"r{c}: R{c}" for c in range(nc))
    s += [f"def g({args}, k: int) -> None:"]
    s += [f"    n{c}: Ptr[R{c}] = r{c}" for c in range(nc)]
    s += ["    i = 0", "    while i < 3:"]
    for c in range(nc):
        s.append(f"        if n{c} is not None:")
        for i in range(nf):
            s += [f"            if k == {i}:", f"                n{c} = n{c}.f{i}"]
    s += ["        i += 1"]
    for c in range(nc):
        s += [f"    if r{c}.v is not None:", f"        if n{c} is not None:", f"            n{c}.reset()",
              f"        print(r{c}.v)"]
    s += ["", "", "g(" + ", ".join(f"R{c}()" for c in range(nc)) + ", 0)"]
    write(f"cost_many_{nc}x{nf}", f"{nc} chained walks over {nf}-field records", s)


def wide(nroots: int, nfacts: int) -> None:
    s = ["class N:", "    v: int | None", "", "    def __init__(self) -> None:", "        self.v = 1", "", "",
         "def g(k: int) -> None:"]
    s += [f"    r{i} = N()" for i in range(nroots)]
    s += ["    t = r0"]
    for i in range(1, nroots):
        s += [f"    if k == {i}:", f"        t = r{i}"]
    for j in range(nfacts):
        s += [f"    if r{j}.v is not None:", f"        print(r{j}.v)"]
    s += ["    if r0.v is not None:", "        t.v = None", "        print(r0.v)", "", "", "g(0)"]
    write(f"cost_wide_{nroots}_{nfacts}", f"one name holding entries under {nroots} roots, {nfacts} facts, one kill", s)


def descent(depth: int) -> None:
    s = ["from tpy import Ptr", "", "", "class T:", "    v: int | None", f"    l: Ptr[{Q}T{Q}]", f"    r: Ptr[{Q}T{Q}]", "",
         "    def __init__(self) -> None:", "        self.v = 1", "        self.l = None", "        self.r = None", "", "",
         "def g(root: T, k: int, opt: int | None) -> None:", "    node: Ptr[T] = root", "    b = k"]
    for _ in range(depth):
        s += ["    if node is not None:", "        node = node.l if b % 2 == 0 else node.r", "        b = b // 2"]
    s += ["    if root.v is not None:", "        if node is not None:", "            node.v = opt", "        print(root.v)",
          "", "", "g(T(), 0, None)"]
    write(f"cost_descent_{depth}", f"binary descent {depth} levels then a store", s)


def generate(out: Path) -> list[Path]:
    """Write every cost row under `out` and return the files."""
    global OUT
    OUT = out
    OUT.mkdir(parents=True, exist_ok=True)
    main()
    return sorted(OUT.glob("cost_*.py"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for nf in (8, 12, 16):
        walk(nf)
    field_chain(1000)
    field_chain(2000)
    alias_chain(1000)
    alias_chain(3000)
    scc(200)
    many(40, 4)
    wide(500, 40)
    descent(20)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        OUT = Path(sys.argv[1]).resolve()
    main()
    print(OUT)
