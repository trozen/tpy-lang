/**
 * `iter_range` / `iter_of` self-check.
 *
 * Every consumer that loops over an arbitrary iterable -- `sorted`, `any`,
 * `all`, `sum`, a comprehension, the construct family -- goes through one
 * adapter, so it is instantiated with every kind of iterable tpyc can hand it:
 *
 *   1. A type with begin()/end() (a heap container, an INLINE container, a
 *      runtime view, a generator-style self-iterator) passes through as the
 *      same object: elements alias the container, and a self-iterator is
 *      advanced in place.
 *   2. Anything else is iterated through the iterator its `__iter__()` returns,
 *      held in the form it came in: a non-const reference is aliased (a
 *      self-iterator record with no begin()/end(), a member the record
 *      delegates to), a value is built in place with no copy or move (a
 *      separate iterator, a move-only one, an immovable one), and a const
 *      reference is aliased too: it steps through a const `__next__`, and one
 *      whose `__next__` mutates does not build, since a copy would step a
 *      stale duplicate.
 *   2b. A type with methods named begin/end that are not an iterator pair is
 *      not a range: it is iterated through `__iter__`.
 *   2c. A resumable frame's iterator slot (`iter_type_t`) is a VALUE: a
 *      stored iterator handed back by reference is copied into it
 *      (BUGS.md#frame-stored-iter-copied), unlike the statement-scoped
 *      adapter above.
 *   3. `__iter__` is user code: it runs exactly once per loop.
 *   4. A type that steps with `__next__()` and has no `__iter__()` is its own
 *      iterator.
 *   5. The consumers built on the adapter answer the same over every kind.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstdint>
#include <cstdio>
#include <expected>
#include <optional>
#include <string>
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

int iter_calls = 0;

// A generator-style self-iterator: begin()/end() from the mixin.
struct MixinCounter : tpy::next_iter_mixin<MixinCounter, int32_t> {
    int32_t i = 0;
    int32_t n;
    explicit MixinCounter(int32_t n) : n(n) {}
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (i >= n) return tpy::make_unexpected(tpy::StopIteration{});
        return ++i;
    }
    MixinCounter& __iter__() { ++iter_calls; return *this; }
};

// A self-iterator record: `__iter__` returns *this, and a record
// gets no begin()/end().
struct Cnt {
    int32_t n;
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (n == 0) return tpy::make_unexpected(tpy::StopIteration{});
        return --n;
    }
    Cnt& __iter__() { ++iter_calls; return *this; }
};

// A record whose `__iter__` returns a separate iterator over its own storage.
struct Bag {
    std::vector<int32_t> items{3, 1, 2};
    auto __iter__() { ++iter_calls; return ::tpy::__iter__(items); }
};

// The same with a const `__iter__`, reached through a const lvalue.
struct ConstBag {
    std::vector<int32_t> items{5, 4};
    auto __iter__() const { return ::tpy::__iter__(items); }
};

// Delegates to a member iterator: the member is advanced in place.
struct Delegating {
    Cnt inner{2};
    Cnt& __iter__() { ++iter_calls; return inner; }
};

// An iterator that steps through a const `__next__` (its cursor is mutable).
struct ConstStepper {
    mutable int32_t n;
    std::expected<int32_t, tpy::StopIteration> __next__() const {
        if (n == 0) return tpy::make_unexpected(tpy::StopIteration{});
        return --n;
    }
};

// Hands back a const reference: aliased, stepped through the const `__next__`.
struct ConstRef {
    ConstStepper shared{3};
    const ConstStepper& __iter__() { return shared; }
};

// Hands back a const reference to an iterator whose `__next__` mutates: no
// loop can step it without copying it.
struct ConstRefMutating {
    Cnt shared{3};
    const Cnt& __iter__() { return shared; }
};

// Methods named begin/end that are not an iterator pair, beside `__iter__`.
struct FakeBeginEnd {
    std::vector<int32_t> items{4, 5};
    int32_t begin() { return 0; }
    int32_t end() { return 1; }
    auto __iter__() { return ::tpy::__iter__(items); }
};

// ... and ones returning an optional: dereferenceable and comparable, but
// not an iterator (no `++`).
struct OptBeginEnd {
    std::vector<int32_t> items{6, 7};
    std::optional<int32_t> begin() { return 1; }
    std::optional<int32_t> end() { return std::nullopt; }
    auto __iter__() { return ::tpy::__iter__(items); }
};

// Iterators that cannot be copied (a `@nocopy` one) or moved at all.
struct MoveOnlyIter {
    int32_t n;
    explicit MoveOnlyIter(int32_t n) : n(n) {}
    MoveOnlyIter(const MoveOnlyIter&) = delete;
    MoveOnlyIter(MoveOnlyIter&&) = default;
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (n == 0) return tpy::make_unexpected(tpy::StopIteration{});
        return n--;
    }
    MoveOnlyIter& __iter__() { return *this; }
};
struct ImmovableIter {
    int32_t n;
    explicit ImmovableIter(int32_t n) : n(n) {}
    ImmovableIter(const ImmovableIter&) = delete;
    ImmovableIter(ImmovableIter&&) = delete;
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (n == 0) return tpy::make_unexpected(tpy::StopIteration{});
        return n--;
    }
    ImmovableIter& __iter__() { return *this; }
};
struct MakesMoveOnly {
    MoveOnlyIter __iter__() { return MoveOnlyIter(2); }
};
struct MakesImmovable {
    ImmovableIter __iter__() { return ImmovableIter(3); }
};

// Steps with `__next__` and has no `__iter__`.
struct BareNext {
    int32_t n = 2;
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (n == 0) return tpy::make_unexpected(tpy::StopIteration{});
        return n--;
    }
};

// A record whose separate iterator lends reference-type elements.
struct Cell {
    int32_t v;
};
struct Cells {
    std::vector<Cell> items{{1}, {2}};
    auto __iter__() { return ::tpy::__iter__(items); }
};

// A record stepping str elements.
struct Words {
    std::vector<std::string> items{"b", "a"};
    auto __iter__() const { return ::tpy::__iter__(items); }
};

struct Pairs {
    std::vector<std::tuple<std::string, int32_t>> items{{"a", 1}, {"b", 2}};
    auto __iter__() const { return ::tpy::__iter__(items); }
};

template<typename X>
std::vector<int32_t> drain(X& x) {
    std::vector<int32_t> out;
    for (auto&& e : ::tpy::iter_range(x)) out.push_back(e);
    return out;
}

using Vec = std::vector<int32_t>;

template<typename X>
constexpr bool passes_through =
    std::is_same_v<decltype(::tpy::iter_range(std::declval<X&>())), X&>;

static_assert(passes_through<Vec>);
static_assert(passes_through<const Vec>);
static_assert(passes_through<std::array<int32_t, 3>>);
static_assert(passes_through<std::string_view>);
static_assert(passes_through<std::span<const int32_t>>);
static_assert(passes_through<MixinCounter>);
static_assert(!passes_through<Cnt>);
static_assert(!passes_through<Bag>);
static_assert(!passes_through<FakeBeginEnd>);
static_assert(!passes_through<OptBeginEnd>);

// A frame's iterator slot is a value: a stored iterator is copied
// (BUGS.md#frame-stored-iter-copied).
static_assert(std::is_same_v<tpy::iter_type_t<Delegating>, Cnt>);
static_assert(std::is_same_v<tpy::iter_type_t<ConstRefMutating>, Cnt>);

// The protocol face keeps the __iter__() result in the form it came in.
template<typename X>
using held_t = decltype(::tpy::iter_range(std::declval<X&>()).it);
static_assert(std::is_same_v<held_t<Cnt>, Cnt&>);
static_assert(std::is_same_v<held_t<Delegating>, Cnt&>);
static_assert(std::is_same_v<held_t<ConstRef>, const ConstStepper&>);
static_assert(std::is_same_v<held_t<MakesMoveOnly>, MoveOnlyIter>);
static_assert(std::is_same_v<held_t<MakesImmovable>, ImmovableIter>);
static_assert(std::is_same_v<held_t<BareNext>, BareNext&>);
static_assert(std::is_same_v<decltype(::tpy::iter_of(std::declval<BareNext&>())),
                             BareNext&>);

void pass_through() {
    Vec v{1, 2, 3};
    check(&*::tpy::iter_range(v).begin() == v.data(),
          "a container passes through: elements alias it");
    std::array<int32_t, 3> a{4, 5, 6};
    check(&*::tpy::iter_range(a).begin() == a.data(),
          "an inline container passes through");
    std::string_view sv = "xy";
    std::string got;
    for (char c : ::tpy::iter_range(sv)) got += c;
    check(got == "xy", "a runtime view iterates its characters");
    MixinCounter g(3);
    iter_calls = 0;
    check(drain(g) == Vec{1, 2, 3}, "a self-iterator with begin/end iterates");
    check(g.i == 3, "a self-iterator with begin/end is advanced in place");
    check(iter_calls == 0, "begin/end is preferred over __iter__");
}

void protocol() {
    iter_calls = 0;
    Cnt c{3};
    check(drain(c) == Vec{2, 1, 0}, "a self-iterator record iterates");
    check(c.n == 0, "a self-iterator record is advanced in place, not copied");
    check(iter_calls == 1, "a self-iterator record's __iter__ runs once");

    iter_calls = 0;
    Bag b;
    check(drain(b) == Vec{3, 1, 2}, "a separate iterator iterates");
    check(iter_calls == 1, "a separate iterator's __iter__ runs once");
    Cells cs;
    for (Cell& e : ::tpy::iter_range(cs)) e.v *= 10;
    check(cs.items[0].v == 10 && cs.items[1].v == 20,
          "a separate iterator's lent elements alias the record's storage");

    const ConstBag cb;
    check(drain(cb) == Vec{5, 4}, "a const lvalue with a const __iter__ iterates");

    iter_calls = 0;
    Delegating d;
    check(drain(d) == Vec{1, 0}, "a delegated member iterator iterates");
    check(d.inner.n == 0, "a delegated member iterator is advanced in place");

    ConstRef cr;
    check(drain(cr) == Vec{2, 1, 0}, "a const-reference iterator iterates");
    check(cr.shared.n == 0, "a const-reference iterator is advanced in place");

    FakeBeginEnd fb;
    check(drain(fb) == Vec{4, 5}, "methods named begin/end do not make a range");
    OptBeginEnd ob;
    check(drain(ob) == Vec{6, 7}, "begin/end returning an optional do not make a range");

    MakesMoveOnly mo;
    check(drain(mo) == Vec{2, 1}, "a move-only iterator is built in place");
    MakesImmovable im;
    check(drain(im) == Vec{3, 2, 1}, "an immovable iterator is built in place");

    BareNext bn;
    check(drain(bn) == Vec{2, 1}, "a __next__-only type is its own iterator");
    check(bn.n == 0, "a __next__-only type is advanced in place");

}

void consumers() {
    Bag b;
    check(tpy::builtin_sorted<int32_t>(b) == Vec{1, 2, 3}, "sorted(user iterable)");
    check(tpy::builtin_sorted_key<int32_t>(b, [](int32_t x) { return -x; })
              == Vec{3, 2, 1}, "sorted(user iterable, key=...)");
    check(tpy::builtin_sum<int32_t>(b) == 6, "sum(user iterable)");
    check(tpy::builtin_sum_start<int32_t>(b, 10) == 16, "sum(user iterable, start)");
    check(tpy::builtin_any(b) && tpy::builtin_all(b), "any/all(user iterable)");
    Cnt c{3};
    check(tpy::builtin_sorted<int32_t>(c) == Vec{0, 1, 2} && c.n == 0,
          "sorted(self-iterator record) drains it");
    check(tpy::builtin_sum<int32_t>(Cnt{4}) == 6, "sum(temporary self-iterator)");
    check(tpy::builtin_sum<int32_t>(MixinCounter(3)) == 6, "sum(temporary generator)");

    iter_calls = 0;
    check((tpy::construct<Vec>(b) == Vec{3, 1, 2}), "list(user iterable)");
    check((tpy::construct<Vec>(Bag{}) == Vec{3, 1, 2}), "list(temporary user iterable)");
    Cnt c2{2};
    check((tpy::construct<Vec>(c2) == Vec{1, 0}) && c2.n == 0,
          "list(self-iterator record) drains it in place");
    check(iter_calls == 3, "list() runs __iter__ once per argument");
    check((tpy::construct<Vec>(BareNext{}) == Vec{2, 1}), "list(__next__-only)");
    check(tpy::construct<std::vector<std::string>>(Words{})
              == std::vector<std::string>{"b", "a"}, "list(str user iterable)");

    auto s = tpy::set_construct<int32_t>(b);
    check(s.size() == 3 && s.contains(1), "set(user iterable)");
    auto m = tpy::dict_construct<std::string, int32_t>(Pairs{});
    check(m.size() == 2 && m.contains("b"), "dict(user iterable of pairs)");

    Vec ext{9};
    tpy::extend(ext, b);
    check((ext == Vec{9, 3, 1, 2}), "extend(container, user iterable)");
    Vec le{8};
    tpy::list_extend(le, b);
    check((le == Vec{8, 3, 1, 2}), "list.extend(user iterable)");
    check(tpy::str_join(",", Words{}) == "b,a", "str.join(user iterable)");
    std::vector<uint8_t> bytes;
    tpy::bytes_extend_int_iterable(bytes, b);
    check((bytes == std::vector<uint8_t>{3, 1, 2}), "bytes(user iterable)");
}

} // namespace

int main() {
    pass_through();
    protocol();
    consumers();
    if (failures == 0) std::printf("ok\n");
    return failures == 0 ? 0 : 1;
}
