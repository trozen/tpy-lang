# A temporary a statement-bound operand reads lives to the end of its
# statement, and an operand bound before it runs only where Python does.
from tpy import int32, StrView
from fixtures import (M, NM, addall, first, head, mk, same, show, side,
                      sum_nm, tick, total)
import part_positions
import part_frames
import part_roots
import part_guards
import part_checks
import part_lowered
import part_held
import part_unwraps


def packs(name: str, flag: bool) -> None:
    fb: StrView = name
    # A view element over a fresh string: the string lives as the array.
    show("pack", name, name + "-tail-long-heap-string-0123456789")  # tpyc: ok
    # A view lent through a call argument.
    show("lent", head(name + "-lent-long-heap-string-0123456789"))  # tpyc: ok
    # A select as a view element: each arm's fresh root seated in its arm.
    show("selpack", (name + "-sel-long-heap-string-0123456789").strip()  # tpyc: ok
         if flag else fb)
    big: int = 10 ** 30
    # A value pack under a guard is built only when the guard passes.
    print("valpack", tick(big, big + 1) if flag else 0)  # tpyc: ok
    print("valpack-skip", (not flag) and tick(big, big) > 0)  # tpyc: ok
    print("strpack", total(name + "x", name) if flag else 0)  # tpyc: ok
    # A view element converted in place from a `char` is its own root.
    show("char", name[0], name)  # tpyc: ok


def guards(flag: bool) -> None:
    # The held left operand runs only behind its guard.
    guard_and_r = flag and (side("L", 0).n or 5) > 0  # tpyc: ok
    print("guard-and", guard_and_r)
    guard_skip_r = (not flag) and (side("skipped", 0).n or 5) > 0  # tpyc: ok
    print("guard-skip", guard_skip_r)
    # An untaken ternary arm does not run.
    tern_r = 3 if flag else (side("arm", 4).n or 6)  # tpyc: ok
    print("tern", tern_r)
    a: list[int32] = [1]
    b: list[int32] = []
    # A reference pack aliases the caller's lists, guarded or not.
    print("refpack", flag and addall(a, b) > 0, a, b)  # tpyc: ok
    print("refpack-skip", (not flag) and addall(a, b) > 0, a, b)  # tpyc: ok
    # A guarded `@nomove` pack is built in place, only when reached.
    nomove_r = flag and sum_nm(NM(1), NM(2)) > 0  # tpyc: ok
    print("nomove", nomove_r)
    nomove_skip_r = 5 if flag else sum_nm(NM(10), NM(20))  # tpyc: ok
    print("nomove-skip", nomove_skip_r)


def roots(name: str) -> None:
    fb: StrView = name
    # A chain and a membership over fresh roots.
    in_r = mk("in", 1).ps[0].n in (1, 2)  # tpyc: ok
    print("in", in_r)
    chain_r = 0 < mk("chain", 5).p.n < 9  # tpyc: ok
    print("chain", chain_r)
    # A three-operand chain whose first pair fails stops there.
    chain3_r = 9 < mk("c3", 1).p.n < mk("c3-skipped", 2).p.n  # tpyc: ok
    print("chain3", chain3_r)
    chain3_pass_r = 0 < mk("c3a", 1).p.n < mk("c3b", 2).p.n < 10  # tpyc: ok
    print("chain3-pass", chain3_pass_r)
    # A needle lent through a call argument, and a select needle.
    in_lent_r = same(mk("same", 1)).p.n in (1, 2)  # tpyc: ok
    print("in-lent", in_lent_r)
    print("in-strip", (name + "  ").strip() in ("ab", "cd"))  # tpyc: ok
    print("in-sel", ((name + "-x").strip() or fb) in ("ab-x", "q"))  # tpyc: ok
    # A held operand lent through a call argument.
    sel_lent_r = first(mk("first", 3).ps).n or 7  # tpyc: ok
    print("sel-lent", sel_lent_r)
    v = head(name + "-or-long-heap-string-0123456789") or fb  # tpyc: ok
    print("sel-head", v, len(v))


def main() -> None:
    packs("ab", True)
    packs("ab", False)
    guards(True)
    guards(False)
    roots("ab")
    part_positions.main()
    part_frames.main()
    part_roots.main()
    part_guards.main()
    part_checks.main()
    part_lowered.main()
    part_held.main()
    part_unwraps.main()


# Module level: the same shapes at global statements.
gname = "gl"
show("module-pack", gname, gname + "-tail-long-heap-string-0123456789")  # tpyc: ok
g_module_in_r = mk("module", 1).ps[0].n in (1, 2)  # tpyc: ok
print("module-in", g_module_in_r)
g_module_guard_r = len(gname) == 2 and (side("module", 0).n or 5) > 0  # tpyc: ok
print("module-guard", g_module_guard_r)
main()
