# Regression: overload-probe LHS-hint seeding must NOT mix hints across
# multiple LHS-matching candidates. Both overloads of `f` return Own[Result],
# so both pass the LHS-match gate. Pre-fix, position 1's per-arg hint was
# taken from the FIRST overload's `str` param (cross-candidate splice),
# which biased the inner Box(Dog(...)) at position 1 to be analyzed under
# hint=str. The hint mismatched Box's record pattern (so it fell through),
# but the inner Box still cached T=Dog, and the cache short-circuit at the
# top of `_analyze_record_constructor` then blocked the post-selection
# retry from refreshing under Box[Pet]. Result was an order-dependent
# outcome: with the overloads in THIS order, the call failed; with them
# reversed, the call succeeded -- a silent declaration-order-dependent
# resolution that's the real correctness bug.
#
# Post-fix: when 2+ candidates' returns match the LHS hint, the probe skips
# per-arg seeding entirely and falls back to unhinted analysis. Both
# overloads then see the same `Box[Dog]` for both args and reject
# consistently, regardless of declaration order. This is the desired
# property -- order-independence -- but it's a deliberate trade-off:
# semantically the second overload `f(Own[Box[Pet]], Own[Box[Pet]])`
# WOULD match (Dog conforms to dynamic Pet via Adapter wrap), and the
# pre-fix B-first ordering proved this. The conservative gate over-rejects
# in cases where multi-candidate consensus would work, in exchange for
# eliminating the declaration-order-dependence. The principled fix
# (per-candidate trial-and-rollback at the probe, regime-C style for non-Fn
# args) is filed in TODO.md as `bidir-hint follow-ups`. Until that lands,
# the user workaround is to write the inner ctors with explicit type args:
# `f(Box[Pet](Dog("Rex")), Box[Pet](Dog("Spot")))`.
from typing import Protocol
from tpy import dynamic, Own, dispatch
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Result:
    pass


@dispatch
def f(x: Own[Box[Pet]], y: str) -> Own[Result]:
    return Result()


@dispatch
def f(x: Own[Box[Pet]], y: Own[Box[Pet]]) -> Own[Result]:
    return Result()


def main() -> None:
    r: Result = f(Box(Dog("Rex")), Box(Dog("Spot")))  # tpyc: error(/No matching overload/)
    print(r)


main()
