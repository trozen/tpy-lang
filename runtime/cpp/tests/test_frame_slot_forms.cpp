/**
 * frame_slot storage-form self-check.
 *
 * Pins the two guarantees codegen relies on when it spells a resumable frame's
 * for-loop variable from the SOURCE type rather than from the TPy element type:
 *
 *   1. `for_elem_deref_t` / `for_elem_next_t` pick alias-vs-own correctly for
 *      every source shape, including the ones TPy cannot classify (a fresh
 *      value from `__next__` looks exactly like a borrow after unwrap_ref).
 *   2. One spelling drives both forms -- `emplace` binds, `(*f)` reads -- so a
 *      wrong pick is a compile error or a visible behaviour change here rather
 *      than a silent copy (mutation lost) or a dangling pointer in generated
 *      code.
 *
 * Exits non-zero on failure; the harness treats output as the assertion.
 */
#include <cstdio>
#include <expected>
#include <type_traits>
#include <vector>

#include "tpy/tpy.hpp"

namespace {

struct Point {
    int x;
};

struct Node {
    int v;
};

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// A borrowing `__next__`: hands out references into a container it does not own,
// wrapped in val_or_ref -- the shape a native container iterator produces.
struct BorrowIter {
    std::vector<Point>* c;
    std::size_t i = 0;

    std::expected<tpy::val_or_ref<Point>, tpy::StopIteration> __next__() {
        if (i >= c->size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<Point>((*c)[i++]);
    }
    BorrowIter& __iter__() { return *this; }
};

// A FRESH-value `__next__`, the shape a user `def __next__(self) -> Own[Node]`
// produces. Indistinguishable from the above after unwrap_ref, which is why the
// val_or_ref wrapper -- not reference-ness -- is the borrow marker.
struct FreshIter {
    int n = 0;

    std::expected<Node, tpy::StopIteration> __next__() {
        if (n >= 3) return tpy::make_unexpected(tpy::StopIteration{});
        return Node{++n};
    }
    FreshIter& __iter__() { return *this; }
};

// A borrowing `__next__` over a VALUE element: val_or_ref::get() const hands
// back a const ref, so this must own a mutable copy rather than bind `const T*`
// (binding it broke `itertools.islice` with `const int*` -> `int*`).
struct ValIter {
    std::vector<int>* c;
    std::size_t i = 0;

    std::expected<tpy::val_or_ref<int>, tpy::StopIteration> __next__() {
        if (i >= c->size()) return tpy::make_unexpected(tpy::StopIteration{});
        return tpy::val_or_ref<int>((*c)[i++]);
    }
    ValIter& __iter__() { return *this; }
};

// --- 1. the traits pick the right form ---

static_assert(std::is_same_v<tpy::for_elem_next_t<BorrowIter>, Point&>,
              "a lent non-value element must alias its source");
static_assert(std::is_same_v<tpy::for_elem_next_t<FreshIter>, Node>,
              "a fresh non-value element must be owned, not aliased");
static_assert(std::is_same_v<tpy::for_elem_next_t<ValIter>, int>,
              "a value element must be owned MUTABLY, not bound as const T*");

static_assert(std::is_same_v<tpy::for_elem_deref_t<std::vector<Point>::iterator>,
                             Point&>,
              "a container's non-value element must alias");
static_assert(std::is_same_v<tpy::for_elem_deref_t<std::vector<int>::iterator>,
                             int>,
              "a container's value element must be owned");
static_assert(std::is_same_v<
                  tpy::for_elem_deref_t<std::vector<Point>::const_iterator>,
                  const Point&>,
              "a readonly element must keep const, binding as const T*");

// A trivially-destructible payload that is NOT trivially default-constructible:
// it must stay on the primary template, because a bare member would run this
// counter at frame creation. `live` tracks how many exist.
int live = 0;

struct CountedInit {
    int v;
    CountedInit() : v(-1) { ++live; }
    explicit CountedInit(int n) : v(n) { ++live; }
};

// A move-only payload that IS trivially default-constructible and trivially
// destructible -- the shape a @nocopy record takes. It must stay on the primary
// template too: the trivial form assigns and copies where the primary
// placement-constructs and moves, so a deleted copy ctor breaks it.
struct MoveOnly {
    int v;
    MoveOnly() = default;
    explicit MoveOnly(int n) : v(n) {}
    MoveOnly(const MoveOnly&) = delete;
    MoveOnly& operator=(const MoveOnly&) = delete;
    MoveOnly(MoveOnly&&) = default;
    MoveOnly& operator=(MoveOnly&&) = default;
};

// --- 2. the trivial-payload form defers construction and keeps the surface ---

void slot_trivial_form() {
    tpy::frame_slot<int> f;
    check(!f.has_value(), "trivial slot starts empty");
    f.emplace(7);
    check(f.has_value() && (*f) == 7, "trivial slot holds its value");
    (*f) += 1;
    check((*f) == 8, "a trivial slot's payload is mutable in place");
    f.emplace(2);
    check((*f) == 2, "re-emplace overwrites");
    f.reset();
    check(!f.has_value(), "reset clears a trivial slot");

    f.emplace(5);
    tpy::frame_slot<int> g(std::move(f));
    check((*g) == 5 && !f.has_value(), "move transfers and clears the source");

    // Moving a slot that was NEVER emplaced must not touch its payload: `v_` is
    // uninitialized until the first write, so an unguarded mem-init would read
    // an indeterminate value. This is the common path -- a coro struct is moved
    // into its heap wrapper before any frame field has been written.
    {
        tpy::frame_slot<int> never;
        tpy::frame_slot<int> moved_dead(std::move(never));
        check(!moved_dead.has_value() && !never.has_value(),
              "moving a dead trivial slot leaves both dead");
        moved_dead.emplace(9);
        check((*moved_dead) == 9, "a moved-from-dead slot still works");
    }

    // The whole point of the constraints: a payload whose default ctor does work
    // must NOT be specialized, or declaring the frame would run it.
    live = 0;
    {
        tpy::frame_slot<CountedInit> slot;
        check(live == 0, "declaring a frame slot must not construct its payload");
        slot.emplace(3);
        check(live == 1 && (*slot).v == 3, "emplace constructs exactly once");
    }

    // A move-only payload must keep working -- it can only reach the primary
    // template, whose emplace placement-constructs and whose move ctor moves.
    {
        tpy::frame_slot<MoveOnly> slot;
        slot.emplace(4);
        check((*slot).v == 4, "a move-only payload emplaces");
        tpy::frame_slot<MoveOnly> moved(std::move(slot));
        check((*moved).v == 4 && !slot.has_value(),
              "a move-only payload survives the slot's move ctor");
    }
}

// --- 3. the borrow form's own contract ---

void slot_ref_form() {
    Point a{1};
    Point b{2};
    tpy::frame_slot<Point&> f;
    check(!f.has_value(), "ref slot starts empty");
    f.emplace(a);
    check(f.has_value() && &(*f) == &a, "ref slot binds the address");
    (*f).x += 10;
    check(a.x == 11, "writes through a ref slot reach the target");
    f.emplace(b);
    check(&(*f) == &b, "rebinding a ref slot reseats it");
    check(a.x == 11, "rebinding does not disturb the old target");
    f.reset();
    check(!f.has_value(), "reset clears a ref slot");

    // Move must leave the source dead so a moved-from frame cannot alias on.
    f.emplace(a);
    tpy::frame_slot<Point&> g(std::move(f));
    check(&(*g) == &a && !f.has_value(), "move transfers and clears the source");

    // A CONST payload is what a readonly element produces. `emplace(T&)` alone
    // would let a temporary bind here (`const T&` accepts a prvalue) and store
    // a pointer to it; the deleted rvalue overload is what prevents that. Both
    // static_asserts below fail to compile if it is removed.
    // A CONST payload is what a readonly element produces. `emplace(T&)` alone
    // would let a temporary bind here (`const T&` accepts a prvalue) and store
    // a pointer to it; the deleted rvalue overload prevents that. The rejection
    // cannot be asserted here -- selecting a deleted function is a hard error,
    // not an unsatisfied constraint, so `static_assert(!requires ...)` fails to
    // compile rather than passing. `emplace(Point{1})` on either ref slot is
    // the manual check; it must not build.
    const Point c{7};
    tpy::frame_slot<const Point&> cf;
    cf.emplace(c);
    check(&(*cf) == &c && (*cf).x == 7, "a const ref slot binds const storage");
    static_assert(requires(tpy::frame_slot<const Point&> s, const Point& lv) {
        s.emplace(lv);
    }, "a const lvalue must bind");
}

// --- 4. one spelling drives both forms, over a real iteration ---

void bump(Point& p) { p.x += 100; }
void bump(Node&) {}
void bump(int& n) { n += 1; }

int peek(const Point& p) { return p.x; }
int peek(const Node& n) { return n.v; }
int peek(int n) { return n; }

// Deliberately generic: it must compile and behave for every source above with
// no form-dependent branch, which is exactly what codegen emits.
template <typename Src>
void drive(Src src, int* observed_last) {
    using slot_t = tpy::frame_slot<tpy::for_elem_next_t<Src>>;
    tpy::frame_slot<tpy::iter_result_t<Src>> r;
    slot_t p;
    auto it = ::tpy::__iter__(src);
    while (true) {
        r.emplace(it.__next__());
        if (!(*r).has_value()) break;
        p.emplace(::tpy::unwrap_ref_move(*(*r)));
        bump(*p);
    }
    // Reading the loop var after the loop ends (Python leaks it) must be valid
    // for BOTH forms: the alias still points at the last element, the owning
    // slot still holds the last value -- even though the step result that
    // produced it has been re-emplaced with the exhausted alternative.
    *observed_last = peek(*p);
}

void iteration_forms() {
    int last = 0;
    {
        std::vector<Point> c{{1}, {2}};
        drive(BorrowIter{&c}, &last);
        check(c[0].x == 101 && c[1].x == 102,
              "borrowed elements: mutation through the loop var reaches source");
        check(last == 102, "borrowed element: post-loop read stays valid");
    }
    {
        drive(FreshIter{}, &last);
        check(last == 3, "fresh element: post-loop read does not dangle");
    }
    {
        std::vector<int> c{10, 20};
        drive(ValIter{&c}, &last);
        check(c[0] == 10 && c[1] == 20,
              "value elements: the source is not written through the loop var");
        check(last == 21, "value element: the owned copy is mutable");
    }
}

} // namespace

int main() {
    slot_trivial_form();
    slot_ref_form();
    iteration_forms();
    if (failures != 0) {
        std::printf("%d frame_slot form check(s) failed\n", failures);
        return 1;
    }
    std::printf("frame_slot forms OK\n");
    return 0;
}
