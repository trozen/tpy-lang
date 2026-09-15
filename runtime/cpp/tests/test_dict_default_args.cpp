/**
 * dict default-argument deduction self-check.
 *
 * `d.get(k, x)`, `d.pop(k, x)` and `d.setdefault(k, x)` receive the default in
 * whatever form the language gives that value at an argument: a `str` default
 * is a view or a bare literal, a `bytes` one a span, a BigInt one still an
 * `int`. None of those IS the dict's value type, so the three natives must
 * deduce V from the map alone and build the owned V themselves.
 *
 * No generated case reaches every spelling (a missing one is a toolchain error
 * with no TPy location, which is how all three regressed before), so the
 * spellings are pinned here at their source.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <string>
#include <string_view>

#include "tpy/tpy.hpp"

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// A value type that reports what the three natives do to the STORED object on
// the hit path -- the fact no exact copy/move count can pin portably (NRVO is
// the toolchain's choice), but the one the language prescribes: `get` hands
// back an independent value, `setdefault` hands back the node itself.
struct Counted {
    std::string v;
    static int copies;

    Counted(std::string_view s) : v(s) {}
    Counted(const Counted& o) : v(o.v) { ++copies; }
    Counted(Counted&& o) noexcept = default;
    Counted& operator=(const Counted& o) { v = o.v; ++copies; return *this; }
    Counted& operator=(Counted&&) noexcept = default;
};

int Counted::copies = 0;

}  // namespace

int main() {
    ::tpy::ordered_map<std::string, std::string> s;
    std::string_view view = "view";

    check(::tpy::dict_get_default(s, "k", "lit") == "lit",
          "get: bare literal default at a str slot");
    check(::tpy::dict_get_default(s, "k", view) == "view",
          "get: string_view default at a str slot");
    check(::tpy::dict_pop_default(s, "k", view) == "view",
          "pop: string_view default at a str slot");
    check(::tpy::dict_setdefault(s, "k", view) == "view",
          "setdefault: string_view default at a str slot");
    check(s.size() == 1, "setdefault stored the value it built");
    // The reference is the stored node, not a copy: CPython's setdefault hands
    // back the object in the dict, and `d.setdefault(k, []).append(x)` relies
    // on it.
    ::tpy::dict_setdefault(s, "k", view) += "!";
    check(*::tpy::dict_get(s, "k") == "view!",
          "setdefault returns the stored node");

    // A bare string literal at setdefault's default: `V` is deduced from the
    // map only, so the `const char[N]` cannot conflict with it.
    check(::tpy::dict_setdefault(s, "lit", "made") == "made",
          "setdefault: bare literal default at a str slot");

    // pop on a HIT returns the stored value and removes the entry; the default
    // is not built at all.
    s.insert_or_assign(std::string("hit"), std::string("stored"));
    check(::tpy::dict_pop_default(s, "hit", view) == "stored",
          "pop: hit returns the stored value");
    check(!s.contains("hit"), "pop: hit erased the entry");

    ::tpy::ordered_map<std::string, ::tpy::Bytes> b;
    ::tpy::Bytes payload;
    payload.push_back(1);
    payload.push_back(2);
    ::tpy::BytesView span(payload);
    check(::tpy::dict_get_default(b, "k", span).size() == 2,
          "get: BytesView default at a bytes slot");
    check(::tpy::dict_setdefault(b, "k", span).size() == 2,
          "setdefault: BytesView default at a bytes slot");

    ::tpy::ordered_map<std::string, ::tpy::BigInt> n;
    check(::tpy::dict_get_default(n, "k", 7) == ::tpy::BigInt(7),
          "get: int literal default at a BigInt slot");
    check(::tpy::dict_setdefault(n, "k", 7) == ::tpy::BigInt(7),
          "setdefault: int literal default at a BigInt slot");

    ::tpy::ordered_map<std::string, Counted> c;
    std::string_view seed = "seed";
    ::tpy::dict_setdefault(c, "k", seed);
    // setdefault on a hit returns the node, so nothing is copied and a write
    // through the result reaches the dict.
    Counted::copies = 0;
    ::tpy::dict_setdefault(c, "k", seed).v = "written";
    check(Counted::copies == 0, "setdefault: hit copies nothing");
    check(::tpy::dict_get(c, "k")->v == "written",
          "setdefault: hit returns the node itself");
    // get on a hit returns a VALUE, so mutating it leaves the dict alone.
    Counted got = ::tpy::dict_get_default(c, "k", seed);
    got.v = "mine";
    check(::tpy::dict_get(c, "k")->v == "written",
          "get: hit returns an independent value");
    // A miss builds the default straight into the returned V.
    check(::tpy::dict_get_default(c, "absent", seed).v == "seed",
          "get: miss builds the default");
    check(::tpy::dict_pop_default(c, "absent", seed).v == "seed",
          "pop: miss builds the default");

    if (failures == 0) std::printf("OK\n");
    return failures == 0 ? 0 : 1;
}
