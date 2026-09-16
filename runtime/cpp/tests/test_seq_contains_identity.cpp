/**
 * seq_contains identity-shortcut self-check.
 *
 * Pins the rule generated code relies on when it spells `::tpy::seq_contains`
 * for a reference-typed needle: containment is Python's `x is e or x == e`,
 * not `==` alone.
 *
 * No snapshot case can pin the negative half -- a needle whose `__eq__` is not
 * reflexive answers correctly only because of the identity leg, and the leg is
 * instantiated per needle/element pairing, so the pairings that must keep the
 * `==`-only answer (a scalar needle, a prvalue-yielding range) have no
 * generated witness at all.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <algorithm>
#include <cstdio>
#include <limits>
#include <ranges>
#include <span>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// A record whose `__eq__` compares a float field, so an instance holding NaN
// is not equal to ITSELF -- the shape `std::ranges::contains` gets wrong.
struct P {
    double v;
    friend bool operator==(const P& a, const P& b) { return a.v == b.v; }
};

}  // namespace

int main() {
    const double nan = std::numeric_limits<double>::quiet_NaN();
    std::vector<P> xs = {P{nan}, P{1.0}};

    check(tpy::seq_contains(xs, xs[0]),
          "a NaN-field element is present in its own container");
    check(!std::ranges::contains(xs, xs[0]),
          "the == -only answer is still wrong (the reason this helper exists)");
    check(tpy::seq_contains(xs, xs[1]),
          "an ordinary element is present by identity and by ==");

    P equal_not_identical{1.0};
    check(tpy::seq_contains(xs, equal_not_identical),
          "an equal-but-distinct needle is present by ==");
    P absent{2.0};
    check(!tpy::seq_contains(xs, absent), "an absent needle is absent");
    P other_nan{nan};
    check(!tpy::seq_contains(xs, other_nan),
          "a DISTINCT NaN needle stays absent -- identity, not NaN-blindness");

    // A borrowed view over the same buffer: the elements are the same objects,
    // so identity still answers.
    std::span<const P> view{xs};
    check(tpy::seq_contains(view, xs[0]),
          "identity holds through a span over the same buffer");

    // A copy is a different object, so only == can answer -- this is where
    // TPy's copy-vs-alias choice becomes visible, and CPython would say True.
    std::vector<P> copy = xs;
    check(!tpy::seq_contains(copy, xs[0]),
          "a COPIED NaN element is absent: identity is address identity");

    // Pairings with no identity to test keep the ==-only answer.
    std::vector<double> ds = {nan, 1.0};
    check(tpy::seq_contains(ds, 1.0), "a scalar needle compares by ==");
    check(!tpy::seq_contains(ds, nan), "a scalar NaN has no identity to test");
    // A range that yields PRVALUES has no element to take the address of, so
    // the identity leg must not be instantiated for it.
    auto made = ds | std::views::transform([](double d) { return P{d}; });
    check(tpy::seq_contains(made, equal_not_identical),
          "a prvalue-yielding range compares by == alone");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
