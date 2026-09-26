/**
 * copy_iter() source-kind self-check.
 *
 * `copy_iter(x)` copies each element `x` yields, and holds `x` the way a
 * combinator holds an argument: an lvalue is BORROWED, a temporary is OWNED.
 *
 *   1. Every element is a copy: a write to one never reaches the source, and
 *      the source is left intact (nothing is moved out of it).
 *   2. A temporary of every kind is iterated, not taken for an iterator: a
 *      container, an inline container (whose iterators do not survive a move,
 *      so the adapter must own it and make its cursor at the first pull), a
 *      dict view, a self-iterator (a combinator), a compiled record whose
 *      `__iter__` returns a separate iterator (pinned: built in place), and one
 *      returning an immovable iterator by value.
 *   3. An lvalue source is advanced in place when its `__iter__` hands back a
 *      non-const reference (a self-iterator, a member the record delegates
 *      to), and through a copy when it hands back a const one.
 *   4. `__iter__` is user code: it runs once, at the copy_iter() call.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <expected>
#include <new>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
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

// A reference type (not a value type): what a compiled record is.
struct Rec {
    int32_t v;
};

using Recs = std::vector<Rec>;
using RecArr = std::array<Rec, 3>;
using Ints = std::vector<int32_t>;

// The order `__iter__` ran in, one digit per call.
int iter_trace = 0;

// A self-iterator: counts 1..n, `__iter__()` returns *this.
struct Counter : tpy::next_iter_mixin<Counter, int32_t> {
    int32_t i = 0;
    int32_t n;
    explicit Counter(int32_t n) : n(n) {}
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (i >= n) return tpy::make_unexpected(tpy::StopIteration{});
        return ++i;
    }
    Counter& __iter__() {
        iter_trace = iter_trace * 10 + n;
        return *this;
    }
};

// A compiled record whose `__iter__()` hands back a separate iterator.
struct Separate {
    static constexpr std::string_view __tpy_class_name__ = "test.Separate";
    int32_t n;
    Counter __iter__() {
        iter_trace = iter_trace * 10 + n;
        return Counter(n);
    }
};

// Returns a reference to a member iterator: CPython advances that object.
struct Delegating {
    static constexpr std::string_view __tpy_class_name__ = "test.Delegating";
    Counter inner;
    explicit Delegating(int32_t n) : inner(n) {}
    Counter& __iter__() { return inner; }
};

// `__iter__` hands back a const reference: nothing can pull through it.
struct ConstIter {
    Counter shared{3};
    const Counter& __iter__() { return shared; }
};

// An iterator that can be neither copied nor moved, returned by value.
struct Immovable : tpy::next_iter_mixin<Immovable, int32_t> {
    int32_t i = 0;
    int32_t n;
    explicit Immovable(int32_t n) : n(n) {}
    Immovable(const Immovable&) = delete;
    Immovable(Immovable&&) = delete;
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (i >= n) return tpy::make_unexpected(tpy::StopIteration{});
        return ++i;
    }
    Immovable& __iter__() { return *this; }
};
struct MakesImmovable {
    static constexpr std::string_view __tpy_class_name__ = "test.MakesImmovable";
    int32_t n;
    Immovable __iter__() { return Immovable(n); }
};

template<typename T, typename CI>
std::vector<T> drain(CI& ci) {
    std::vector<T> out;
    while (true) {
        auto r = ci.__next__();
        if (!r.has_value()) return out;
        out.push_back(std::move(*r));
    }
}

int32_t sum_v(const std::vector<Rec>& rs) {
    int32_t t = 0;
    for (const Rec& r : rs) t += r.v;
    return t;
}

// Builds the adapter in a buffer, moves it out, then destroys the original
// and scribbles over its storage, so a cursor still pointing at the old
// inline array reads garbage.
template<typename Make>
auto relocated(Make make) {
    using It = decltype(make());
    alignas(It) unsigned char buf[sizeof(It)];
    It* made = new (buf) It(make());
    It moved(std::move(*made));
    made->~It();
    std::fill_n(buf, sizeof(It), static_cast<unsigned char>(0xEE));
    return moved;
}

void container_lvalue() {
    Recs src = {{1}, {2}, {3}};
    auto ci = tpy::copy_iter<Rec>(src);
    auto out = drain<Rec>(ci);
    out[0].v = 99;
    check(sum_v(out) == 99 + 2 + 3, "an lvalue container yields every element");
    check(src[0].v == 1, "a write to a copy does not reach the lvalue container");

    const Recs& ro = src;
    auto cc = tpy::copy_iter<Rec>(ro);
    auto out2 = drain<Rec>(cc);
    check(sum_v(out2) == 6, "a const lvalue container yields every element");
}

void container_rvalue() {
    auto ci = tpy::copy_iter<Rec>(Recs{{4}, {5}});
    auto out = drain<Rec>(ci);
    check(sum_v(out) == 9, "a container temporary is owned and iterated");

    auto moved = relocated([] { return tpy::copy_iter<Rec>(RecArr{{{1}, {2}, {3}}}); });
    auto out2 = drain<Rec>(moved);
    check(sum_v(out2) == 6, "an inline container temporary survives a move before the first pull");
}

void dict_view_rvalue() {
    tpy::ordered_map<int32_t, Rec> d({{1, Rec{10}}, {2, Rec{20}}});

    auto vs = tpy::copy_iter<Rec>(tpy::dict_values(d));
    auto vals = drain<Rec>(vs);
    vals[0].v = 99;
    check(sum_v(vals) == 99 + 20, "a values() temporary is iterated");
    check(tpy::__getitem__(d, 1).v == 10, "a write to a copied value does not reach the dict");

    auto ks = tpy::copy_iter<int32_t>(tpy::dict_keys(d));
    auto keys = drain<int32_t>(ks);
    check(keys.size() == 2 && keys[0] == 1 && keys[1] == 2, "a keys() temporary is iterated");

    auto is = tpy::copy_iter<std::tuple<int32_t, Rec>>(tpy::dict_items(d));
    auto items = drain<std::tuple<int32_t, Rec>>(is);
    std::get<1>(items[1]).v = 77;
    check(items.size() == 2 && std::get<0>(items[1]) == 2,
          "an items() temporary is iterated");
    check(tpy::__getitem__(d, 2).v == 20, "a write to a copied item does not reach the dict");
}

void self_iterator_rvalue() {
    Recs src = {{1}, {2}};
    auto ci = tpy::copy_iter<std::tuple<int32_t, Rec>>(tpy::builtin_enumerate(src));
    auto out = drain<std::tuple<int32_t, Rec>>(ci);
    std::get<1>(out[0]).v = 99;
    check(out.size() == 2 && std::get<1>(out[1]).v == 2,
          "an enumerate temporary is iterated");
    check(src[0].v == 1, "a write to a copied tuple element does not reach the source");

    Ints ns = {7, 8};
    auto zi = tpy::copy_iter<std::tuple<int32_t, Rec>>(tpy::builtin_zip(ns, src));
    auto zout = drain<std::tuple<int32_t, Rec>>(zi);
    check(zout.size() == 2 && std::get<0>(zout[1]) == 8 && std::get<1>(zout[1]).v == 2,
          "a zip temporary is iterated");

    iter_trace = 0;
    auto cc = tpy::copy_iter<int32_t>(Counter(3));
    check(iter_trace == 3, "a self-iterator temporary's __iter__ runs at the call");
    check(drain<int32_t>(cc).size() == 3, "a self-iterator temporary is iterated");
}

void self_iterator_lvalue() {
    Counter c(4);
    iter_trace = 0;
    auto ci = tpy::copy_iter<int32_t>(c);
    check(iter_trace == 4, "an lvalue self-iterator's __iter__ runs at the call");
    check(drain<int32_t>(ci).size() == 4, "an lvalue self-iterator is iterated");
    check(c.i == 4, "an lvalue self-iterator is advanced in place, not through a copy");
}

void user_record_sources() {
    iter_trace = 0;
    auto sp = tpy::copy_iter<int32_t>(Separate{3});
    check(iter_trace == 3, "a pinned record temporary's __iter__ runs once, at the call");
    check(drain<int32_t>(sp).size() == 3, "a pinned record temporary is iterated in place");

    Separate s{2};
    iter_trace = 0;
    auto sl = tpy::copy_iter<int32_t>(s);
    check(iter_trace == 2, "an lvalue record's __iter__ runs once, at the call");
    check(drain<int32_t>(sl).size() == 2, "an lvalue record is iterated");

    Delegating d(3);
    auto dl = tpy::copy_iter<int32_t>(d);
    check(drain<int32_t>(dl).size() == 3, "a delegating lvalue is iterated");
    check(d.inner.i == 3, "a member iterator returned by reference is advanced in place");

    auto dr = tpy::copy_iter<int32_t>(Delegating(2));
    auto drn = drain<int32_t>(dr).size();
    check(drn == 2, "a delegating temporary is iterated");

    ConstIter k;
    auto kc = tpy::copy_iter<int32_t>(k);
    check(drain<int32_t>(kc).size() == 3 && k.shared.i == 0,
          "a const-reference iterator is iterated through a copy");

    auto im = tpy::copy_iter<int32_t>(MakesImmovable{2});
    check(drain<int32_t>(im).size() == 2, "an immovable by-value iterator is built in place");
}

void range_for() {
    Recs src = {{1}, {2}};
    int32_t total = 0;
    for (auto& r : tpy::copy_iter<Rec>(Recs{{5}, {6}})) total += r.v;
    for (auto& r : tpy::copy_iter<Rec>(src)) {
        r.v += 100;
        total += r.v;
    }
    check(total == 11 + 101 + 102, "range-for over an owned and a borrowed source");
    check(src[0].v == 1, "range-for hands out copies");
}

}  // namespace

int main() {
    container_lvalue();
    container_rvalue();
    dict_view_rvalue();
    self_iterator_rvalue();
    self_iterator_lvalue();
    user_record_sources();
    range_for();
    if (failures) {
        std::printf("%d failure(s)\n", failures);
        return 1;
    }
    std::printf("ok\n");
    return 0;
}
