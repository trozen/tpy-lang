/**
 * Owning-slot hand-over self-check.
 *
 * A parameter typed `Iterable[Own[T]]` deduces its C++ type from the
 * argument; `hands_over_v<A, S>` decides from that type whether the caller
 * handed the container over, and `handover_iter<A>` / `handover_range<A>` /
 * `resumable_handover_iter_init<A>` then move it into its own consuming
 * iterator. A wrong answer is invisible in a program's output (the lent path
 * copies each element instead), so it is pinned here:
 *
 *   1. `hands_over_v` holds for exactly `std::vector`, `std::array`,
 *      `ordered_set` and `ordered_map` passed by value, and not for a class
 *      derived from one (it has an iteration of its own), a const container
 *      or an lvalue reference (the caller kept it).
 *   2. Each traversal of a container handed over copies no element.
 *   3. `own_iter` reaches a record's consuming `__iter__` (its `&&` member,
 *      with the lending variant's return type or another one, or alone) for
 *      an rvalue only, and refuses a const record, a lending-only record, a
 *      plain container subclass and the bytes family; a frame slot over a
 *      record handed over steps its consuming iterator, copying nothing.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <array>
#include <cstddef>
#include <cstdio>
#include <functional>
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

struct Tracked {
    static inline int copies = 0;
    int v;
    explicit Tracked(int x) : v(x) {}
    Tracked(const Tracked& o) : v(o.v) { ++copies; }
    Tracked(Tracked&& o) noexcept : v(o.v) {}
    Tracked& operator=(const Tracked& o) {
        v = o.v;
        ++copies;
        return *this;
    }
    Tracked& operator=(Tracked&& o) noexcept {
        v = o.v;
        return *this;
    }
    bool operator==(const Tracked& o) const { return v == o.v; }
};

}  // namespace

template<>
struct std::hash<Tracked> {
    std::size_t operator()(const Tracked& t) const noexcept { return std::hash<int>{}(t.v); }
};

namespace {

using Vec = std::vector<Tracked>;
using Arr = std::array<Tracked, 2>;
using Set = tpy::ordered_set<Tracked>;
using Map = tpy::ordered_map<Tracked, int>;

struct DerivedVec : Vec {};

// --- 1. which parameter types hand over ---

static_assert(tpy::hands_over_v<Vec, Vec>, "a list passed by value hands over");
static_assert(tpy::hands_over_v<Arr, Arr>, "an array passed by value hands over");
static_assert(tpy::hands_over_v<Set, Set>, "a set passed by value hands over");
static_assert(tpy::hands_over_v<Map, Map>, "a dict passed by value hands over");
static_assert(!tpy::hands_over_v<DerivedVec, DerivedVec>,
              "a list subclass iterates through its own __iter__");
static_assert(!tpy::hands_over_v<const Vec, const Vec>,
              "a const container cannot be moved from");
static_assert(!tpy::hands_over_v<Vec&, Vec>, "a lent container stays the caller's");

// --- 2. each traversal of a container handed over copies nothing ---

Vec make_vec() {
    Vec c;
    c.emplace_back(1);
    c.emplace_back(2);
    return c;
}

Arr make_arr() { return Arr{Tracked(1), Tracked(2)}; }

Set make_set() {
    Set c;
    c.insert(Tracked(1));
    c.insert(Tracked(2));
    return c;
}

Map make_map() {
    Map c;
    c[Tracked(1)] = 1;
    c[Tracked(2)] = 2;
    return c;
}

template<typename C>
void traversals(C (*make)(), const char* what) {
    char label[96];
    {
        C c = make();
        Tracked::copies = 0;
        auto&& it = tpy::handover_iter<C>(c);
        int sum = 0;
        while (true) {
            auto r = it.__next__();
            if (!r.has_value()) break;
            Tracked t(tpy::unwrap_ref_move(*r));
            sum += t.v;
        }
        std::snprintf(label, sizeof label, "%s: handover_iter copies nothing", what);
        check(sum == 3 && Tracked::copies == 0, label);
    }
    {
        C c = make();
        Tracked::copies = 0;
        Vec out;
        for (auto&& e : tpy::handover_range<C>(c)) {
            out.push_back(std::forward<decltype(e)>(e));
        }
        std::snprintf(label, sizeof label, "%s: handover_range copies nothing", what);
        check(out.size() == 2 && Tracked::copies == 0, label);
    }
    {
        C c = make();
        Tracked::copies = 0;
        tpy::frame_loop_slot<tpy::handover_iter_type_t<C, C>> slot;
        tpy::resumable_handover_iter_init<C>(slot, c);
        int sum = 0;
        while (true) {
            auto r = tpy::resumable_iter_next(slot, c);
            if (!r.has_value()) break;
            Tracked t(tpy::unwrap_ref_move(*r));
            sum += t.v;
        }
        std::snprintf(label, sizeof label,
                      "%s: resumable_handover_iter_init copies nothing", what);
        check(sum == 3 && Tracked::copies == 0, label);
    }
}

// --- 3. records join through their `&&` member ---

// The consuming iterator a record's `&&` `__iter__` returns: it owns the
// record's elements and hands each one out by move.
struct BagIter {
    Vec items;
    std::size_t pos = 0;
    std::expected<Tracked, tpy::StopIteration> __next__() {
        if (pos >= items.size()) return tpy::make_unexpected(tpy::StopIteration{});
        return std::move(items[pos++]);
    }
    BagIter& __iter__() { return *this; }
};

// The lending variant returns another type than the consuming one.
struct BagPair {
    Vec items = make_vec();
    auto __iter__() const& { return tpy::__iter__(items); }
    BagIter __iter__() && { return BagIter{std::move(items)}; }
};

// Both variants return the same type (each builds its own BagIter).
struct BagSame {
    Vec items = make_vec();
    BagIter __iter__() & { return BagIter{items}; }
    BagIter __iter__() && { return BagIter{std::move(items)}; }
};

// The only `__iter__` consumes.
struct BagOnly {
    Vec items = make_vec();
    BagIter __iter__() && { return BagIter{std::move(items)}; }
};

struct BagLending {
    Vec items = make_vec();
    auto __iter__() & { return tpy::__iter__(items); }
    auto __iter__() const& { return tpy::__iter__(items); }
};

struct BagPlain {
    Vec items = make_vec();
    auto __iter__() const { return tpy::__iter__(items); }
};

// A subclass of a class with a consuming `__iter__`: sema gives it none of
// its own (BUGS.md#consuming-iter-not-inherited), and the base's `&&`
// member would take the object as the base.
struct BagOnlySub : BagOnly {};

template<typename T>
concept own_iter_takes = requires(T&& x) { tpy::own_iter(std::forward<T>(x)); };

static_assert(own_iter_takes<BagPair>, "a record's && __iter__ is its consuming iteration");
static_assert(own_iter_takes<BagSame>, "the && member answers whatever it returns");
static_assert(own_iter_takes<BagOnly>, "a consuming-only record is taken");
static_assert(std::is_same_v<decltype(tpy::own_iter(std::declval<BagPair>())), BagIter>,
              "own_iter calls the && member");
static_assert(!own_iter_takes<BagOnlySub>, "only a && __iter__ T declares is its own");
static_assert(!own_iter_takes<BagPair&>, "an lvalue record stays the caller's");
static_assert(!own_iter_takes<const BagPair>, "a const record cannot be given up");
static_assert(!own_iter_takes<BagLending>, "a lending-only & / const& pair does not consume");
static_assert(!own_iter_takes<BagPlain>, "an unqualified lending __iter__ does not consume");
static_assert(!own_iter_takes<DerivedVec>, "a plain container subclass has no consuming iteration");
static_assert(!own_iter_takes<tpy::Bytes>, "bytes have no consuming iteration");
static_assert(!own_iter_takes<tpy::ByteArray>, "a bytearray has no consuming iteration");
static_assert(tpy::hands_over_v<BagPair, BagPair>, "a && record passed by value hands over");
static_assert(tpy::hands_over_v<BagOnly, BagOnly>, "a consuming-only record passed by value hands over");
static_assert(!tpy::hands_over_v<BagPair&, BagPair>, "a lent record stays the caller's");
static_assert(!tpy::hands_over_v<BagLending, BagLending>,
              "a lending-only record passed by value iterates as itself");

template<typename C>
void record_traversals(const char* what) {
    char label[96];
    {
        C c;
        Tracked::copies = 0;
        auto&& it = tpy::handover_iter<C>(c);
        int sum = 0;
        while (true) {
            auto r = it.__next__();
            if (!r.has_value()) break;
            Tracked t(tpy::unwrap_ref_move(*r));
            sum += t.v;
        }
        std::snprintf(label, sizeof label, "%s: handover_iter copies nothing", what);
        check(sum == 3 && Tracked::copies == 0 && c.items.empty(), label);
    }
    {
        C c;
        Tracked::copies = 0;
        tpy::frame_loop_slot<tpy::handover_iter_type_t<C, C>> slot;
        tpy::resumable_handover_iter_init<C>(slot, c);
        int sum = 0;
        while (true) {
            auto r = tpy::resumable_handover_iter_next<C>(slot, c);
            if (!r.has_value()) break;
            Tracked t(tpy::unwrap_ref_move(*r));
            sum += t.v;
        }
        std::snprintf(label, sizeof label,
                      "%s: resumable_handover_iter_* copy nothing", what);
        check(sum == 3 && Tracked::copies == 0 && c.items.empty(), label);
    }
}

// A consuming iterator whose own `__iter__` is observable (a user iterator
// class): a builtin consumer of the record handed over asks the record's
// `__iter__` once and steps what it returned, never that iterator's
// `__iter__` (CPython's `iter()` runs once) -- also when the iterator class
// merely has methods named `begin` / `end` (a TPy record can).
template<bool NamedBeginEnd>
struct CountingIter {
    static inline int iters = 0;
    int n = 0;
    std::expected<int, tpy::StopIteration> __next__() {
        if (n == 2) return tpy::make_unexpected(tpy::StopIteration{});
        return ++n;
    }
    CountingIter& __iter__() { ++iters; return *this; }
};

template<>
struct CountingIter<true> {
    static inline int iters = 0;
    int n = 0;
    std::expected<int, tpy::StopIteration> __next__() {
        if (n == 2) return tpy::make_unexpected(tpy::StopIteration{});
        return ++n;
    }
    CountingIter& __iter__() { ++iters; return *this; }
    int begin() { return 7; }
    int end() { return 8; }
};

template<bool NamedBeginEnd>
struct CountingSource {
    CountingIter<NamedBeginEnd> __iter__() && { return CountingIter<NamedBeginEnd>{}; }
};

template<bool NamedBeginEnd>
void single_iter_call(const char* what) {
    using Iter = CountingIter<NamedBeginEnd>;
    using Source = CountingSource<NamedBeginEnd>;
    char label[128];
    Iter::iters = 0;
    std::vector<int> xs;
    tpy::list_extend(xs, Source{});
    std::snprintf(label, sizeof label,
                  "%s: extend steps the consuming iterator without its __iter__", what);
    check(xs.size() == 2 && Iter::iters == 0, label);
    auto ys = tpy::construct<std::vector<int>>(Source{});
    std::snprintf(label, sizeof label,
                  "%s: list() steps the consuming iterator without its __iter__", what);
    check(ys.size() == 2 && Iter::iters == 0, label);
    auto zs = tpy::builtin_sorted<int>(Source{});
    std::snprintf(label, sizeof label,
                  "%s: sorted() steps the consuming iterator without its __iter__", what);
    check(zs.size() == 2 && Iter::iters == 0, label);
    // CPython's slice assignment lists the iterator `iter()` handed back,
    // which asks it its own `__iter__` once more (`PySequence_Fast`).
    std::vector<int> ws = {9, 9, 9};
    tpy::list_set_slice(ws, tpy::BasicSlice{1, 2}, Source{});
    std::snprintf(label, sizeof label,
                  "%s: a slice store asks the consuming iterator's __iter__ as CPython does", what);
    check(ws.size() == 4 && Iter::iters == 1, label);
}

// `reserve_if_sized` asks only the runtime's own consuming iterators: a
// user iterator class answering `size()` with a `std::size_t` (a TPy
// `uint64`) is never called, as CPython runs no user code to presize --
// whatever members it declares: the runtime's own are named by a `tpy::`
// trait (`sized_consumer`).
struct SizedUserIter {
    static inline int size_calls = 0;
    static constexpr bool tpy_sized_consumer = true;
    std::size_t size() const { ++size_calls; return 100; }
};

void reserve_only_runtime_iters() {
    std::vector<int> out;
    SizedUserIter::size_calls = 0;
    tpy::reserve_if_sized(out, SizedUserIter{});
    check(SizedUserIter::size_calls == 0 && out.capacity() == 0,
          "a user iterator's size() is never asked");
    auto it = tpy::own_iter(make_vec());
    std::vector<Tracked> moved;
    tpy::reserve_if_sized(moved, it);
    check(it.size() == 2 && moved.capacity() >= 2, "a runtime consuming iterator reserves");
    auto set_it = tpy::own_iter(make_set());
    std::vector<Tracked> from_set;
    tpy::reserve_if_sized(from_set, set_it);
    check(from_set.capacity() >= 2, "a set's consuming iterator reserves");
    auto map_it = tpy::own_iter(make_map());
    std::vector<Tracked> from_map;
    tpy::reserve_if_sized(from_map, map_it);
    check(from_map.capacity() >= 2, "a dict's consuming iterator reserves");
    // Past a step, only what is left is reserved (`reserve(n)` on an empty
    // vector allocates n in libstdc++ and libc++ alike).
    auto stepped = tpy::own_iter(make_vec());
    auto first = stepped.__next__();
    std::vector<Tracked> rest_vec;
    tpy::reserve_if_sized(rest_vec, stepped);
    check(first.has_value() && stepped.size() == 1 && rest_vec.capacity() == 1,
          "a list's consuming iterator reserves what is left after a step");
    auto stepped_set = tpy::own_iter(make_set());
    auto first_key = stepped_set.__next__();
    std::vector<Tracked> rest_set;
    tpy::reserve_if_sized(rest_set, stepped_set);
    check(first_key.has_value() && rest_set.capacity() == 1,
          "a set's consuming iterator reserves what is left after a step");
    auto stepped_map = tpy::own_iter(make_map());
    auto first_entry = stepped_map.__next__();
    std::vector<Tracked> rest_map;
    tpy::reserve_if_sized(rest_map, stepped_map);
    check(first_entry.has_value() && rest_map.capacity() == 1,
          "a dict's consuming iterator reserves what is left after a step");
}

}  // namespace

int main() {
    single_iter_call<false>("iterator class");
    single_iter_call<true>("iterator class with begin/end methods");
    reserve_only_runtime_iters();
    traversals<Vec>(make_vec, "list");
    traversals<Arr>(make_arr, "array");
    traversals<Set>(make_set, "set");
    traversals<Map>(make_map, "dict");
    record_traversals<BagPair>("record pair");
    record_traversals<BagSame>("record same-type pair");
    record_traversals<BagOnly>("consuming-only record");
    if (failures != 0) {
        std::printf("%d hand-over check(s) failed\n", failures);
        return 1;
    }
    std::printf("hand-over forms OK\n");
    return 0;
}
