#pragma once
// Runtime support for exposing a TPy class (record) as a CPython heap type.
//
// An exposed instance embeds the TPy C++ payload after the PyObject header
// and reaches it through a payload POINTER (Instance<T>::p), which enables a
// second instance flavor: a BORROW VIEW, whose p points into another exposed
// instance's storage (a class-typed field of a holder) while a strong ref on
// the holder keeps that storage alive (the memoryview owner-keepalive
// pattern). An owned instance's PyObject owns the payload, so multiple Python
// references alias it and see mutations (correct Python reference semantics
// -- unlike the by-copy container boundary); a view aliases the holder's live
// field the same way, so mutation through the view writes through. The
// generated glue builds the type via PyType_FromSpec, wiring tp_init
// (placement-new the payload from marshalled __init__ args), tp_dealloc
// (instance_dealloc<T>), and per-method/per-field wrappers. The class-typed
// marshalling here is named (not to_py/from_py overloads) because T is a user
// type unknown to marshal.hpp: the glue calls these with the module-static
// PyTypeObject* for the class.
//
// IN  (param): instance_payload<T> type-checks and returns a borrow of the
//              live payload -- mutations through it write through to the
//              same object (param aliasing holds; a view param borrows the
//              underlying field storage).
// OUT (return): instance_to_py<T> allocates a NEW owned instance and moves
//              the value into its payload (the explicit-copy Own[...] form);
//              borrow_to_py<T> mints (or, via the per-module ViewRegistry,
//              re-returns) a borrow view of a live field, preserving identity
//              across repeated accesses.

#include <cstddef>  // std::max_align_t (the hierarchy-uniform payload offset)
#include <cstdint>  // std::uintptr_t (within_payload's well-defined compare)
#include <memory>   // std::destroy_at (the generated tp_init re-init path)
#include <new>
#include <type_traits>
#include <unordered_map>
#include <utility>
#include <vector>

#include "tpy/interop/cpython_h.hpp"
#include "tpy/interop/marshal.hpp"  // MarshalError + the scalar marshallers

namespace tpy::interop {

// The instance layout: PyObject header, a constructed-payload flag, the view
// bookkeeping (owner + payload pointer), then the inline storage. Every
// wrapper reads the payload through `p`: an OWNED instance has p == &storage
// and owner == nullptr; a BORROW VIEW has p pointing into its owner's storage
// and owner holding a strong reference on that holder (a view's own `storage`
// is dead weight -- PyType_GenericAlloc allocates tp_basicsize regardless --
// accepted for layout uniformity). tp_basicsize == sizeof(Instance<T>).
//
// `initialized` distinguishes a constructed OWNED payload from the
// zero-filled storage PyType_GenericAlloc hands out: an instance can exist
// with an unconstructed payload (tp_new/GenericAlloc ran but tp_init did not
// -- e.g. `Cls.__new__(Cls)`), and tp_init can run more than once (an
// explicit `obj.__init__(...)`). Both the destructor and re-init must consult
// it so they never destroy a payload that was never constructed, nor leak one
// being overwritten. A view keeps initialized == false (its storage is never
// constructed); `owner` is the flavor discriminator.
//
// Inheritance contract: a base-emitted wrapper may receive a derived instance
// and read it through Instance<Base>. That is sound because (a) every member
// before `storage` is T-independent, so `p` sits at one offset for the whole
// hierarchy (and `storage` is pinned by the alignas, guarded by the alignment
// assert), and (b) the Base subobject sits at offset 0 of a derived payload
// -- guaranteed by the Itanium C++ ABI for a single non-virtual base
// (GCC/Clang are already the compiler floor; polymorphic payloads, whose
// vptr would displace the base, are rejected below and the boundary
// validator rejects multiple exposed bases). A derived payload is not
// standard-layout, so the casts here lean on that ABI layout rather than the
// standard's blessing.
template <class T>
struct Instance {
    static_assert(!std::is_polymorphic_v<T>,
                  "an exposed class payload must not be polymorphic -- a vptr "
                  "would displace the base subobject the boundary casts rely on");
    static_assert(alignof(T) <= alignof(std::max_align_t),
                  "an over-aligned exposed class payload would break the "
                  "hierarchy-uniform payload offset");
    cpy::PyObject ob_base;
    bool initialized;
    cpy::PyObject *owner;
    T *p;
    alignas(std::max_align_t) T storage;
};

// Per-module registry of live BORROW VIEWS, keyed by the borrowed storage
// address: repeated accesses to the same field hand back the SAME view
// PyObject (so `h.get_inner() is h.get_inner()`, `==`, and `hash` match
// plain Python). Values are borrowed pointers -- a view deregisters itself
// in instance_dealloc, so an entry never outlives its view. One address can
// legitimately carry several views of UNRELATED types (a first field starts
// at offset 0 of its holder, recursively), hence the small per-address
// bucket; lookups match by inheritance-relatedness, mirroring the glue's
// type-scoped alias candidates. Unlocked: all access is GIL-serialized (the
// glue uses single-phase module init, which forces the GIL even on
// free-threaded CPython builds).
using ViewRegistry =
    std::unordered_map<void *, std::vector<cpy::PyObject *>>;

// tp_dealloc: a view drops its registry entry and releases the owner (the
// borrowed storage is the owner's to destroy); an owned instance runs the
// C++ destructor (so Own/Box/Rc/container fields release). Then free the
// block via the type's tp_free slot. Wired per class (the glue passes the
// module's ViewRegistry). Skips the destructor when the payload was never
// constructed (allocated-but-not-tp_init'd), which would otherwise run ~T
// over zero-filled storage.
template <class T>
void instance_dealloc(cpy::PyObject *self, ViewRegistry &reg) {
    auto *inst = reinterpret_cast<Instance<T> *>(self);
    if (inst->owner != nullptr) {
        auto it = reg.find(inst->p);
        if (it != reg.end()) {
            auto &bucket = it->second;
            for (auto vit = bucket.begin(); vit != bucket.end(); ++vit) {
                if (*vit == self) {
                    bucket.erase(vit);
                    break;
                }
            }
            if (bucket.empty()) {
                reg.erase(it);
            }
        }
        cpy::Py_DecRef(inst->owner);
    } else if (inst->initialized) {
        inst->p->~T();
    }
    using free_fn = void (*)(void *);
    auto fn = reinterpret_cast<free_fn>(
        cpy::PyType_GetSlot(cpy::Py_TYPE(self), cpy::Py_tp_free));
    fn(self);
}

// Borrow the live payload after a type check (exact type or subtype).
// On a type mismatch, set TypeError and throw MarshalError (the boundary
// catch returns the NULL sentinel, preserving the exception).
template <class T>
T *instance_payload(cpy::PyObject *o, cpy::PyTypeObject *type) {
    cpy::PyTypeObject *ot = cpy::Py_TYPE(o);
    if (ot != type && cpy::PyType_IsSubtype(ot, type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError,
                             "expected an instance of the exposed class");
        throw MarshalError{};
    }
    return reinterpret_cast<Instance<T> *>(o)->p;
}

// Mint a fresh OWNED instance of `type` and move `value` into its embedded
// storage. tp_alloc (PyType_GenericAlloc) zero-fills the block (so
// `initialized` starts false and `owner` null); the placement-new over the
// storage is what actually constructs T. The nothrow requirement keeps this
// leak-free without a guard: a throwing move would orphan the freshly
// allocated PyObject (and the destructor can't run, the payload being
// half-built), so reject such a payload at instantiation instead.
template <class T>
cpy::PyObject *instance_to_py(cpy::PyTypeObject *type, T value) {
    static_assert(std::is_nothrow_move_constructible_v<T>,
                  "an exposed class payload must be nothrow-move-constructible "
                  "to cross the boundary by value without a leak on throw");
    cpy::PyObject *o = cpy::PyType_GenericAlloc(type, 0);
    if (o == nullptr) {
        throw MarshalError{};
    }
    auto *inst = reinterpret_cast<Instance<T> *>(o);
    new (&inst->storage) T(std::move(value));
    inst->p = &inst->storage;
    inst->initialized = true;
    return o;
}

// Whether `addr` lies inside the object at [base, base + size): the glue's
// borrow-view owner scan asks which boundary-crossed candidate's payload
// CONTAINS a returned field reference (an exact base match is a first field
// at offset 0 of an inheritance-UNRELATED holder -- the identity path's
// type-scoped compare already claimed the related cases). uintptr_t
// arithmetic keeps the cross-object comparison well-defined.
inline bool within_payload(const void *addr, const void *base,
                           std::size_t size) {
    auto a = reinterpret_cast<std::uintptr_t>(addr);
    auto b = reinterpret_cast<std::uintptr_t>(base);
    return a >= b && a - b < size;
}

// Whether any live borrow view aliases storage inside [base, base + size).
// Guards the generated tp_init's re-init path: destroying + reconstructing a
// holder's payload under a live view would silently swap the value the view
// observes (CPython's re-__init__ rebinds attributes, leaving previously
// obtained references untouched), so the re-init is rejected loudly instead.
// Linear over the registry -- explicit `obj.__init__(...)` re-init is a rare
// path, and modules hold few live views.
inline bool has_views_into(const void *base, std::size_t size,
                           const ViewRegistry &reg) {
    for (const auto &entry : reg) {
        if (within_payload(entry.first, base, size) && !entry.second.empty()) {
            return true;
        }
    }
    return false;
}

// Mint (or re-return) a BORROW VIEW of `ref`, a live class-typed field
// inside `owner`'s storage: a PyObject of the field's exposed `type` whose
// payload pointer aliases the field, holding a strong reference on `owner`
// so the storage outlives the view. The registry lookup preserves identity
// across repeated accesses; entries at the same address match only when
// inheritance-related to the requested type (an UNRELATED exposed class can
// share the address -- a first field starts at offset 0 of its holder -- and
// must get its own view, never someone else's).
template <class T>
cpy::PyObject *borrow_to_py(cpy::PyTypeObject *type, T &ref,
                            cpy::PyObject *owner, ViewRegistry &reg) {
    // A readonly[Cls] borrow return deduces T = const Cls; the view sheds
    // the const like the identity path does -- readonly is a TPy-side
    // no-mutation-through-this-handle contract, and the Python consumer
    // gets an ordinary aliasing object either way (matching the lib/cpy
    // stubs, where readonly is a no-op).
    using U = std::remove_const_t<T>;
    U &target = const_cast<U &>(ref);
    auto &bucket = reg[static_cast<void *>(&target)];
    for (cpy::PyObject *v : bucket) {
        cpy::PyTypeObject *vt = cpy::Py_TYPE(v);
        if (vt == type || cpy::PyType_IsSubtype(vt, type) != 0
            || cpy::PyType_IsSubtype(type, vt) != 0) {
            cpy::Py_IncRef(v);
            return v;
        }
    }
    // Reserve BEFORE allocating the PyObject: a bad_alloc from the registry
    // insertion after the view exists (and the owner ref is taken) would
    // leak both -- with capacity guaranteed, the push_back below cannot
    // throw. (A bucket left empty on a later failure is inert.)
    bucket.reserve(bucket.size() + 1);
    cpy::PyObject *o = cpy::PyType_GenericAlloc(type, 0);
    if (o == nullptr) {
        throw MarshalError{};
    }
    auto *inst = reinterpret_cast<Instance<U> *>(o);
    inst->p = &target;
    inst->owner = owner;
    cpy::Py_IncRef(owner);
    bucket.push_back(o);
    return o;
}

}  // namespace tpy::interop
