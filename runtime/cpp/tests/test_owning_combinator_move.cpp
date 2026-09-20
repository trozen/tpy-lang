/**
 * Owning-combinator move self-check.
 *
 * `enumerate` / `zip` / `map` / `filter` / `reversed` over a TEMPORARY own
 * their source. A consumer that stores one (another combinator, a generator
 * frame) moves it in, so every owning flavor must be movable until its first
 * pull -- and a wrong move is a dangling iterator, never a diagnostic:
 *
 *   1. Every owning flavor is move-constructible, over a heap container, an
 *      INLINE container (`std::array`, whose iterators do not survive a move)
 *      and a self-iterator.
 *   2. A flavor moved before its first pull iterates the container it now
 *      owns, not the one it was moved from. A random-access container is
 *      walked by POSITION, so it survives a move mid-iteration as well; any
 *      other container gets its iterators at the first pull.
 *   3. A self-iterator source holds no cursor at all, so the HOLDER adds no
 *      self-pointer: a source that is itself relocatable mid-iteration stays
 *      so inside a combinator.
 *   4. An lvalue beside a temporary is borrowed, not copied: a self-iterator
 *      is advanced in place, and the caller's object sees the pulls.
 *   5. `__iter__` is user code: it runs once per source at the combinator
 *      call, in argument order, self-iterators included.
 *   6. A compiled record whose `__iter__()` returns a separate iterator is
 *      PINNED when owned: its cursor is made at the combinator call and the
 *      combinator does not move. A runtime type of the same shape (a dict
 *      view) is owned like a container. "Self" means exactly `S&`: a class
 *      returning a base reference may be delegating to a member.
 *   7. The iterator `__iter__()` hands back is held in the form it came in: a
 *      non-const reference is aliased (a member the record delegates to, or
 *      `*this` typed as a base, is advanced in place, and a non-copyable one
 *      works), a value is built in place with no move -- even when its type
 *      has a converting constructor template that would swallow a wrapper --
 *      and a const reference is copied.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <expected>
#include <list>
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

// Returns a base reference that is NOT *this: it delegates to a member. The
// type is the same as an inherited `return *this`, which is why neither
// counts as a self-iterator.
struct Delegating : Counter {
    static constexpr std::string_view __tpy_class_name__ = "test.Delegating";
    Counter inner;
    explicit Delegating(int32_t n) : Counter(0), inner(n) {}
    Counter& __iter__() {
        iter_trace = iter_trace * 10 + inner.n;
        return inner;
    }
};

// Inherits its `__iter__`, which returns `*this` typed as the base.
struct DerivedCounter : Counter {
    using Counter::Counter;
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
// An iterator type with an unconstrained converting ctor template: it must be
// built from the `__iter__()` result itself, never from a wrapper around it.
struct Greedy : tpy::next_iter_mixin<Greedy, int32_t> {
    int32_t i = 0;
    int32_t n = 7;
    explicit Greedy(int32_t n) : n(n) {}
    template<typename U> explicit Greedy(const U&) {}
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (i >= n) return tpy::make_unexpected(tpy::StopIteration{});
        return ++i;
    }
    Greedy& __iter__() { return *this; }
};
struct MakesGreedy {
    static constexpr std::string_view __tpy_class_name__ = "test.MakesGreedy";
    int32_t n;
    Greedy __iter__() { return Greedy(n); }
};

struct MakesImmovable {
    static constexpr std::string_view __tpy_class_name__ = "test.MakesImmovable";
    int32_t n;
    Immovable __iter__() { return Immovable(n); }
};

// `__iter__` hands back a const reference: nothing can pull through it, so
// the combinator iterates a copy.
struct ConstIter {
    Counter shared{3};
    const Counter& __iter__() { return shared; }
};

// A compiled record (tagged the way tpyc tags one) whose `__iter__()` hands
// back a separate iterator object.
struct Separate {
    static constexpr std::string_view __tpy_class_name__ = "test.Separate";
    int32_t n;
    Counter __iter__() {
        iter_trace = iter_trace * 10 + n;
        return Counter(n);
    }
};

// The same shape on a runtime type: untagged.
struct RuntimeView {
    int32_t n;
    Counter __iter__() { return Counter(n); }
};

// A compiled record whose `__iter__` returns a fresh instance of its own
// class by value: not a self-iterator.
struct FreshSelf : tpy::next_iter_mixin<FreshSelf, int32_t> {
    static constexpr std::string_view __tpy_class_name__ = "test.FreshSelf";
    std::expected<int32_t, tpy::StopIteration> __next__() {
        return tpy::make_unexpected(tpy::StopIteration{});
    }
    FreshSelf __iter__() { return FreshSelf{}; }
};

using Arr = std::array<int32_t, 3>;
using Vec = std::vector<int32_t>;

bool odd(int32_t v) { return v % 2 != 0; }
int32_t twice(int32_t v) { return v * 2; }
int32_t add(int32_t a, int32_t b) { return a + b; }

static_assert(tpy::is_self_iterator_v<Counter>);
static_assert(!tpy::is_self_iterator_v<Delegating>);
static_assert(tpy::detail::separate_user_iterator<Delegating>);
static_assert(!tpy::is_self_iterator_v<Vec>);
static_assert(!tpy::is_self_iterator_v<FreshSelf>);
static_assert(tpy::detail::separate_user_iterator<Separate>);
static_assert(tpy::detail::separate_user_iterator<FreshSelf>);
static_assert(!tpy::detail::separate_user_iterator<RuntimeView>);
static_assert(!tpy::detail::separate_user_iterator<Counter>);
static_assert(!tpy::detail::separate_user_iterator<Vec>);
static_assert(!tpy::detail::separate_user_iterator<Arr>);

template<typename T>
constexpr bool movable = std::is_move_constructible_v<T>;

template<typename Src>
constexpr bool all_flavors_movable =
    movable<decltype(tpy::builtin_enumerate<int32_t>(std::declval<Src>()))>
    && movable<decltype(tpy::builtin_zip<int32_t, int32_t>(
           std::declval<Src>(), std::declval<Vec&>()))>
    && movable<decltype(tpy::builtin_map<int32_t, int32_t>(
           &twice, std::declval<Src>()))>
    && movable<decltype(tpy::builtin_map_n<int32_t>(
           &add, std::declval<Src>(), std::declval<Vec&>()))>
    && movable<decltype(tpy::builtin_filter<int32_t>(&odd, std::declval<Src>()))>;

static_assert(all_flavors_movable<Vec>);
static_assert(all_flavors_movable<Arr>);
static_assert(all_flavors_movable<Counter>);
static_assert(all_flavors_movable<RuntimeView>);
// Pinned: the cursor may point into the record, so the combinator stays put.
static_assert(!movable<decltype(tpy::builtin_enumerate<int32_t>(std::declval<Separate>()))>);
static_assert(!movable<decltype(tpy::builtin_zip<int32_t, int32_t>(
                  std::declval<Separate>(), std::declval<Vec&>()))>);
static_assert(movable<decltype(tpy::builtin_reversed<int32_t>(std::declval<Vec>()))>);
static_assert(movable<decltype(tpy::builtin_reversed<int32_t>(std::declval<Arr>()))>);
// No owning flavor copies: a copy would duplicate the owned source.
static_assert(!std::is_copy_constructible_v<
              decltype(tpy::builtin_reversed<int32_t>(std::declval<Vec>()))>);
static_assert(!std::is_copy_constructible_v<
              decltype(tpy::builtin_enumerate<int32_t>(std::declval<Vec>()))>);
// A container that moves without throwing keeps its combinator nothrow-movable,
// which the uninitialized-storage slots require.
static_assert(std::is_nothrow_move_constructible_v<
              decltype(tpy::builtin_enumerate<int32_t>(std::declval<Vec>()))>);
static_assert(std::is_nothrow_move_constructible_v<
              decltype(tpy::builtin_filter<int32_t>(&odd, std::declval<Arr>()))>);

template<typename It>
int32_t drain_sum(It& it) {
    int32_t total = 0;
    while (true) {
        auto r = it.__next__();
        if (!r.has_value()) return total;
        total += tpy::unwrap_ref(*r);
    }
}

// Builds the combinator in a buffer, moves it out, then destroys the original
// and scribbles over its storage, so a cursor still pointing at the old
// inline array reads garbage. `pulls` steps are taken before the move.
template<typename Make>
auto relocated(Make make, int pulls = 0) {
    using It = decltype(make());
    alignas(It) unsigned char buf[sizeof(It)];
    It* made = new (buf) It(make());
    for (int i = 0; i < pulls; ++i) (void)made->__next__();
    It moved(std::move(*made));
    made->~It();
    std::fill_n(buf, sizeof(It), static_cast<unsigned char>(0xEE));
    return moved;
}

void moved_before_first_pull() {
    {
        auto m = relocated([] { return tpy::builtin_enumerate<int32_t>(Arr{5, 6, 7}); });
        int32_t total = 0;
        while (true) {
            auto r = m.__next__();
            if (!r.has_value()) break;
            total += std::get<0>(*r) * 100 + tpy::unwrap_ref(std::get<1>(*r));
        }
        check(total == 5 + 106 + 207, "enumerate over an inline array after a move");
    }
    {
        auto m = relocated([] { return tpy::builtin_filter<int32_t>(&odd, Arr{5, 6, 7}); });
        check(drain_sum(m) == 12, "filter over an inline array after a move");
    }
    {
        auto m = relocated(
            [] { return tpy::builtin_map<int32_t, int32_t>(&twice, Arr{5, 6, 7}); });
        check(drain_sum(m) == 36, "map over an inline array after a move");
    }
    {
        Vec ys = {10, 20, 30};
        auto m = relocated(
            [&] { return tpy::builtin_zip<int32_t, int32_t>(Arr{1, 2, 3}, ys); });
        int32_t total = 0;
        while (true) {
            auto r = m.__next__();
            if (!r.has_value()) break;
            total += std::get<0>(*r) * std::get<1>(*r);
        }
        check(total == 10 + 40 + 90, "zip owning an inline array after a move");
    }
    {
        // Not random access: this container's cursor is a pair of iterators
        // made at the first pull, where an array or a vector keeps a position.
        auto m = relocated(
            [] { return tpy::builtin_filter<int32_t>(&odd, std::list<int32_t>{5, 6, 7}); });
        check(drain_sum(m) == 12, "filter over a bidirectional container after a move");
    }
    {
        // A position survives a move in the middle of the iteration too.
        auto m = relocated(
            [] { return tpy::builtin_enumerate<int32_t>(Arr{5, 6, 7}); }, /*pulls=*/1);
        auto second = m.__next__();
        check(second.has_value() && std::get<0>(*second) == 1
                  && tpy::unwrap_ref(std::get<1>(*second)) == 6,
              "enumerate over an inline array after a mid-iteration move");
    }
    {
        auto m = relocated([] { return tpy::builtin_reversed<int32_t>(Arr{1, 2, 3}); });
        auto first = m.__next__();
        check(first.has_value() && *first == 3, "reversed over an inline array after a move");
    }
}

void self_iterator_moves_mid_iteration() {
    auto m = relocated(
        [] { return tpy::builtin_enumerate<int32_t>(Counter(4)); }, /*pulls=*/1);
    auto second = m.__next__();
    check(second.has_value() && std::get<0>(*second) == 1 && std::get<1>(*second) == 2,
          "a self-iterator source continues after a mid-iteration move");
}

void lvalue_self_iterator_is_not_copied() {
    Counter c(5);
    auto z = tpy::builtin_zip<int32_t, int32_t>(c, Vec{7, 8});
    while (z.__next__().has_value()) {}
    // zip stops at the shorter side after pulling a third element from `c`.
    check(c.i == 3, "zip advances the caller's self-iterator, not a copy");

    Counter d(5);
    auto m = tpy::builtin_map_n<int32_t>(&add, d, Vec{7, 8});
    while (m.__next__().has_value()) {}
    check(d.i == 3, "map_n advances the caller's self-iterator, not a copy");

    // the all-lvalue flavors
    Counter e1(2), e2(2), e3(3), e4(2), e5(2);
    Vec two = {7, 8};
    auto en = tpy::builtin_enumerate<int32_t>(e1);
    while (en.__next__().has_value()) {}
    check(e1.i == 2, "enumerate over an lvalue self-iterator");
    auto mp = tpy::builtin_map<int32_t, int32_t>(&twice, e2);
    while (mp.__next__().has_value()) {}
    check(e2.i == 2, "map over an lvalue self-iterator");
    auto fl = tpy::builtin_filter<int32_t>(&odd, e3);
    while (fl.__next__().has_value()) {}
    check(e3.i == 3, "filter over an lvalue self-iterator");
    auto mn = tpy::builtin_map_n<int32_t>(&add, e4, two);
    while (mn.__next__().has_value()) {}
    check(e4.i == 2, "all-lvalue map_n over an lvalue self-iterator");
    auto zp = tpy::builtin_zip<int32_t, int32_t>(e5, e1);
    while (zp.__next__().has_value()) {}
    check(e5.i == 1, "all-lvalue zip stops after the pull that found the other side spent");
}

void iter_result_is_held_as_returned() {
    // a member the record delegates to, beside a temporary: advanced in place
    Delegating d(3);
    auto z = tpy::builtin_zip<int32_t, int32_t>(d, Vec{7, 8});
    while (z.__next__().has_value()) {}
    check(d.inner.i == 3 && d.i == 0, "a delegated member iterator is aliased, not copied");

    // `*this` typed as a base: no slicing copy
    DerivedCounter dc(5);
    auto y = tpy::builtin_zip<int32_t, int32_t>(dc, Vec{7, 8});
    while (y.__next__().has_value()) {}
    check(dc.i == 3, "an inherited __iter__ advances the caller's object");
    DerivedCounter dc2(2);
    auto en = tpy::builtin_enumerate<int32_t>(dc2);
    while (en.__next__().has_value()) {}
    check(dc2.i == 2, "and so does the all-lvalue flavor");

    // an immovable iterator returned by value is built in place
    auto im = tpy::builtin_enumerate<int32_t>(MakesImmovable{2});
    int32_t total = 0;
    while (true) {
        auto r = im.__next__();
        if (!r.has_value()) break;
        total += std::get<1>(*r);
    }
    check(total == 3, "an immovable by-value iterator is elided into the holder");

    // a converting ctor template on the iterator type is not a way in
    auto gr = tpy::builtin_enumerate<int32_t>(MakesGreedy{3});
    int32_t greedy_pulls = 0;
    while (gr.__next__().has_value()) ++greedy_pulls;
    check(greedy_pulls == 3, "the cursor is built from the __iter__ result, not a wrapper");
    MakesGreedy mg{3};
    auto gz = tpy::builtin_zip<int32_t, int32_t>(mg, Vec{1, 2, 3, 4, 5, 6, 7, 8, 9});
    greedy_pulls = 0;
    while (gz.__next__().has_value()) ++greedy_pulls;
    check(greedy_pulls == 3, "also in a borrowed source");

    // a const reference is copied, in the mixed and the all-lvalue flavor
    ConstIter ci;
    auto zc = tpy::builtin_zip<int32_t, int32_t>(ci, Vec{7, 8, 9, 10});
    int32_t pulled = 0;
    while (zc.__next__().has_value()) ++pulled;
    check(pulled == 3 && ci.shared.i == 0, "a const-reference iterator is iterated through a copy");
    Vec four = {7, 8, 9, 10};
    auto zl = tpy::builtin_zip<int32_t, int32_t>(ci, four);
    pulled = 0;
    while (zl.__next__().has_value()) ++pulled;
    check(pulled == 3, "also by the all-lvalue flavor");
}

void pinned_source_iterates_in_place() {
    iter_trace = 0;
    // Built in place by guaranteed elision; never moved.
    auto z = tpy::builtin_zip<int32_t, int32_t>(Separate{3}, Vec{7, 8, 9});
    check(iter_trace == 3, "a pinned source runs __iter__ at the combinator call");
    int32_t total = 0;
    while (true) {
        auto r = z.__next__();
        if (!r.has_value()) break;
        total += std::get<0>(*r) * std::get<1>(*r);
    }
    check(total == 7 + 16 + 27, "and iterates the iterator __iter__ returned");

    iter_trace = 0;
    auto d = tpy::builtin_enumerate<int32_t>(Delegating(2));
    check(iter_trace == 2, "a delegating record runs its own __iter__");
    auto first = d.__next__();
    check(first.has_value() && std::get<1>(*first) == 1,
          "and pulls the member it delegates to, not itself");
}

void iter_runs_once_in_argument_order() {
    iter_trace = 0;
    auto z = tpy::builtin_zip<int32_t, int32_t, int32_t>(Counter(1), Counter(2), Counter(3));
    check(iter_trace == 123, "owning zip runs __iter__ left to right, before any pull");
    while (z.__next__().has_value()) {}
    check(iter_trace == 123, "and never again");

    iter_trace = 0;
    Counter a(4), b(5);
    auto y = tpy::builtin_zip<int32_t, int32_t>(a, b);
    check(iter_trace == 45, "the all-lvalue zip runs __iter__ left to right");
    (void)y;

    iter_trace = 0;
    Counter c(6);
    auto w = tpy::builtin_zip<int32_t, int32_t>(c, Counter(7));
    check(iter_trace == 67, "a mixed zip runs __iter__ left to right");
    (void)w;

    iter_trace = 0;
    auto e = relocated([] { return tpy::builtin_enumerate<int32_t>(Counter(8)); });
    check(iter_trace == 8, "a move before the first pull does not run __iter__ again");
    (void)e;

    iter_trace = 0;
    auto v = tpy::builtin_enumerate<int32_t>(RuntimeView{2});
    check(iter_trace == 0, "a runtime view's cursor waits for the first pull");
    auto first = v.__next__();
    check(first.has_value() && std::get<1>(*first) == 1, "and is made there");
}

} // namespace

int main() {
    moved_before_first_pull();
    self_iterator_moves_mid_iteration();
    lvalue_self_iterator_is_not_copied();
    iter_runs_once_in_argument_order();
    pinned_source_iterates_in_place();
    iter_result_is_held_as_returned();
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
