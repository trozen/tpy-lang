/**
 * TurboPython Runtime - Ordered Set
 *
 * Hash set that preserves insertion order (matching Python's set implementation).
 * Combines std::unordered_set for O(1) lookup with an intrusive doubly-linked
 * list for insertion-order iteration.
 */

#pragma once

#include <cstdint>
#include <expected>
#include <functional>
#include <initializer_list>
#include <unordered_map>
#include <utility>

#include "core.hpp"

namespace tpy {

template<typename T>
class ordered_set {
    struct Node {
        T value;
        Node* prev = nullptr;
        Node* next = nullptr;

        template<typename U>
        explicit Node(U&& v) : value(std::forward<U>(v)) {}
    };

public:
    // -- Iterator (insertion order) ------------------------------------------

    template<bool IsConst>
    class iterator_impl {
        friend class ordered_set;
        using NodePtr = std::conditional_t<IsConst, const Node*, Node*>;
        NodePtr node_;

        explicit iterator_impl(NodePtr n) : node_(n) {}

    public:
        iterator_impl() : node_(nullptr) {}

        using iterator_category = std::forward_iterator_tag;
        using difference_type = std::ptrdiff_t;
        using value_type = T;
        using reference = const T&;

        const T& operator*() const { return node_->value; }

        iterator_impl& operator++() { node_ = node_->next; return *this; }
        iterator_impl operator++(int) { auto tmp = *this; node_ = node_->next; return tmp; }

        bool operator==(const iterator_impl& o) const { return node_ == o.node_; }
        bool operator!=(const iterator_impl& o) const { return node_ != o.node_; }
    };

    using iterator = iterator_impl<false>;
    using const_iterator = iterator_impl<true>;

    // -- Construction --------------------------------------------------------

    ordered_set() = default;

    ordered_set(std::initializer_list<T> init) {
        for (auto& v : init) {
            insert(v);
        }
    }

    ordered_set(const ordered_set& other) {
        for (auto* n = other.head_; n != nullptr; n = n->next) {
            insert(n->value);
        }
    }

    ordered_set(ordered_set&& other) noexcept
        : table_(std::move(other.table_))
        , head_(other.head_)
        , tail_(other.tail_)
    {
        other.head_ = nullptr;
        other.tail_ = nullptr;
    }

    ordered_set& operator=(const ordered_set& other) {
        if (this != &other) {
            clear();
            for (auto* n = other.head_; n != nullptr; n = n->next) {
                insert(n->value);
            }
        }
        return *this;
    }

    ordered_set& operator=(ordered_set&& other) noexcept {
        if (this != &other) {
            clear();
            table_ = std::move(other.table_);
            head_ = other.head_;
            tail_ = other.tail_;
            other.head_ = nullptr;
            other.tail_ = nullptr;
        }
        return *this;
    }

    ~ordered_set() {
        clear();
    }

    // -- Capacity ------------------------------------------------------------

    int32_t size() const { return static_cast<int32_t>(table_.size()); }
    bool empty() const { return table_.empty(); }

    // -- Modifiers -----------------------------------------------------------

    bool insert(const T& value) {
        auto it = table_.find(value);
        if (it != table_.end()) return false;
        auto* node = new Node(value);
        link_back(node);
        table_.emplace(value, node);
        return true;
    }

    bool insert(T&& value) {
        auto it = table_.find(value);
        if (it != table_.end()) return false;
        auto* node = new Node(std::move(value));
        link_back(node);
        table_.emplace(node->value, node);
        return true;
    }

    bool erase(const T& value) {
        auto it = table_.find(value);
        if (it == table_.end()) return false;
        Node* node = it->second;
        table_.erase(it);
        unlink(node);
        delete node;
        return true;
    }

    void clear() {
        auto* n = head_;
        while (n != nullptr) {
            auto* next = n->next;
            delete n;
            n = next;
        }
        table_.clear();
        head_ = nullptr;
        tail_ = nullptr;
    }

    // -- Lookup --------------------------------------------------------------

    bool contains(const T& value) const {
        return table_.find(value) != table_.end();
    }

    // -- Iteration (insertion order) -----------------------------------------

    iterator begin() { return iterator(head_); }
    iterator end() { return iterator(nullptr); }
    const_iterator begin() const { return const_iterator(head_); }
    const_iterator end() const { return const_iterator(nullptr); }

    // -- Python __iter__() support -------------------------------------------

    // Defined in set_ops.hpp (needs native_iterator template)
    inline auto __iter__() const;

    // -- Comparison (order-independent, matching Python) ---------------------

    bool operator==(const ordered_set& other) const {
        if (table_.size() != other.table_.size()) return false;
        for (auto* n = head_; n != nullptr; n = n->next) {
            if (!other.contains(n->value)) return false;
        }
        return true;
    }

    bool operator!=(const ordered_set& other) const {
        return !(*this == other);
    }

    // Python set comparison: <= means subset, < means strict subset, etc.
    bool operator<=(const ordered_set& other) const {
        if (table_.size() > other.table_.size()) return false;
        for (auto* n = head_; n != nullptr; n = n->next) {
            if (!other.contains(n->value)) return false;
        }
        return true;
    }

    bool operator<(const ordered_set& other) const {
        return table_.size() < other.table_.size() && *this <= other;
    }

    bool operator>=(const ordered_set& other) const {
        return other <= *this;
    }

    bool operator>(const ordered_set& other) const {
        return other < *this;
    }

    // -- Front access (for pop) ----------------------------------------------

    const T& front() const { return head_->value; }

    void pop_front() {
        Node* node = head_;
        table_.erase(node->value);
        unlink(node);
        delete node;
    }

private:
    void link_back(Node* node) {
        node->prev = tail_;
        node->next = nullptr;
        if (tail_) {
            tail_->next = node;
        } else {
            head_ = node;
        }
        tail_ = node;
    }

    void unlink(Node* node) {
        if (node->prev) {
            node->prev->next = node->next;
        } else {
            head_ = node->next;
        }
        if (node->next) {
            node->next->prev = node->prev;
        } else {
            tail_ = node->prev;
        }
    }

    template<typename> friend struct OwnIterSet;

    std::unordered_map<T, Node*, std::hash<T>> table_;
    Node* head_ = nullptr;
    Node* tail_ = nullptr;
};

// ---------------------------------------------------------------------------
// OwnIterSet -- drain iterator for ordered_set
//
// Owns a moved ordered_set and yields elements by move. O(1) construction
// (hash table move is pointer swap). Implements both C++ range (begin/end
// with move iterators) and the TurboPython Iterator protocol (__next__).
// ---------------------------------------------------------------------------

template<typename T>
struct OwnIterSet {
    ordered_set<T> data;
    typename ordered_set<T>::Node* pos;

    explicit OwnIterSet(ordered_set<T>&& s)
        : data(std::move(s)), pos(data.head_) {}

    OwnIterSet(const OwnIterSet&) = delete;
    OwnIterSet& operator=(const OwnIterSet&) = delete;
    OwnIterSet(OwnIterSet&&) = default;
    OwnIterSet& operator=(OwnIterSet&&) = default;

    // Move iterator: walks linked-list nodes, yields T&& (moved values).
    // Single-pass: each dereference moves the value out of the node.
    struct move_iter {
        using Node = typename ordered_set<T>::Node;
        Node* node_;

        using iterator_category = std::input_iterator_tag;
        using value_type = T;
        using difference_type = std::ptrdiff_t;
        using reference = T&&;

        move_iter() : node_(nullptr) {}
        explicit move_iter(Node* n) : node_(n) {}

        T&& operator*() { return std::move(node_->value); }
        move_iter& operator++() { node_ = node_->next; return *this; }
        move_iter operator++(int) { auto tmp = *this; ++*this; return tmp; }
        bool operator==(const move_iter& o) const { return node_ == o.node_; }
        bool operator!=(const move_iter& o) const { return node_ != o.node_; }
    };

    // NativeIterable: range-based iteration with move semantics.
    // Do not mix range-based (begin/end) and __next__-based iteration on the
    // same instance -- both advance shared state and the result is undefined.
    move_iter begin() { return move_iter(pos); }
    move_iter end()   { return move_iter(nullptr); }

    // TPy Iterator protocol
    std::expected<T, StopIteration> __next__() {
        if (pos == nullptr) return tpy::make_unexpected(StopIteration{});
        T value = std::move(pos->value);
        pos = pos->next;
        return value;
    }

    OwnIterSet& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const OwnIterSet&) {
        return os << "<own_iter_set>";
    }
};

template<typename T>
OwnIterSet<T> own_iter_set(ordered_set<T>&& s) {
    return OwnIterSet<T>{std::move(s)};
}

}  // namespace tpy
