/**
 * TurboPython Runtime - File I/O
 *
 * TextFile: text-mode file wrapper for the open() builtin.
 * Supports read/write/append modes, readline, readlines,
 * and context manager protocol (__enter__/__exit__).
 */

#pragma once

#include "core.hpp"

#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace tpy {

class TextFile {
    std::fstream fs_;
    std::string path_;
    bool readable_ = false;
    bool writable_ = false;
    bool closed_ = false;

public:
    TextFile(std::string_view path, std::string_view mode) : path_(path) {
        std::ios_base::openmode m{};
        if (mode == "r" || mode == "rt") {
            m = std::ios::in;
            readable_ = true;
        } else if (mode == "w" || mode == "wt") {
            m = std::ios::out | std::ios::trunc;
            writable_ = true;
        } else if (mode == "a" || mode == "at") {
            m = std::ios::out | std::ios::app;
            writable_ = true;
        } else if (mode == "r+" || mode == "r+t" || mode == "rt+") {
            m = std::ios::in | std::ios::out;
            readable_ = true;
            writable_ = true;
        } else if (mode == "w+" || mode == "w+t" || mode == "wt+") {
            m = std::ios::in | std::ios::out | std::ios::trunc;
            readable_ = true;
            writable_ = true;
        } else if (mode == "a+" || mode == "a+t" || mode == "at+") {
            m = std::ios::in | std::ios::out | std::ios::app;
            readable_ = true;
            writable_ = true;
        } else if (mode == "x" || mode == "xt") {
            m = std::ios::out | std::ios::trunc | std::ios::noreplace;
            writable_ = true;
        } else if (mode == "x+" || mode == "x+t" || mode == "xt+") {
            m = std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace;
            readable_ = true;
            writable_ = true;
        } else {
            tpy_panic(("open(): unsupported mode '" + std::string(mode) + "'").c_str());
        }
        fs_.open(path_, m);
        if (!fs_.is_open()) {
            tpy_panic(("open(): cannot open '" + std::string(path) + "'").c_str());
        }
    }

    TextFile(TextFile&&) = default;
    TextFile& operator=(TextFile&&) = default;
    TextFile(const TextFile&) = delete;
    TextFile& operator=(const TextFile&) = delete;

    std::string read() {
        if (!readable_) tpy_panic("read(): file not opened for reading");
        std::ostringstream ss;
        ss << fs_.rdbuf();
        return ss.str();
    }

    int32_t write(std::string_view text) {
        if (!writable_) tpy_panic("write(): file not opened for writing");
        fs_ << text;
        return static_cast<int32_t>(text.size());
    }

    std::string readline() {
        if (!readable_) tpy_panic("readline(): file not opened for reading");
        std::string line;
        if (!std::getline(fs_, line)) {
            return "";
        }
        // getline strips the newline delimiter; restore it unless we hit EOF
        // without a trailing newline (Python compat).
        if (!fs_.eof()) {
            line += '\n';
        }
        return line;
    }

    std::vector<std::string> readlines() {
        if (!readable_) tpy_panic("readlines(): file not opened for reading");
        std::vector<std::string> lines;
        std::string line;
        while (std::getline(fs_, line)) {
            if (!fs_.eof()) line += '\n';
            lines.push_back(std::move(line));
        }
        return lines;
    }

    void close() {
        if (!closed_) {
            fs_.close();
            closed_ = true;
        }
    }

    TextFile& __enter__() { return *this; }
    void __exit__() { close(); }

    friend std::ostream& operator<<(std::ostream& os, const TextFile& f) {
        return os << "<TextIO '" << f.path_ << "'>";
    }

    ~TextFile() { close(); }
};

class BinaryFile {
    std::fstream fs_;
    std::string path_;
    bool readable_ = false;
    bool writable_ = false;
    bool closed_ = false;

public:
    BinaryFile(std::string_view path, std::string_view mode) : path_(path) {
        std::ios_base::openmode m = std::ios::binary;
        if (mode == "rb") {
            m |= std::ios::in;
            readable_ = true;
        } else if (mode == "wb") {
            m |= std::ios::out | std::ios::trunc;
            writable_ = true;
        } else if (mode == "ab") {
            m |= std::ios::out | std::ios::app;
            writable_ = true;
        } else if (mode == "r+b" || mode == "rb+") {
            m |= std::ios::in | std::ios::out;
            readable_ = true;
            writable_ = true;
        } else if (mode == "w+b" || mode == "wb+") {
            m |= std::ios::in | std::ios::out | std::ios::trunc;
            readable_ = true;
            writable_ = true;
        } else if (mode == "a+b" || mode == "ab+") {
            m |= std::ios::in | std::ios::out | std::ios::app;
            readable_ = true;
            writable_ = true;
        } else if (mode == "xb") {
            m |= std::ios::out | std::ios::trunc | std::ios::noreplace;
            writable_ = true;
        } else if (mode == "x+b" || mode == "xb+") {
            m |= std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace;
            readable_ = true;
            writable_ = true;
        } else {
            tpy_panic(("open(): unsupported binary mode '" + std::string(mode) + "'").c_str());
        }
        fs_.open(path_, m);
        if (!fs_.is_open()) {
            tpy_panic(("open(): cannot open '" + std::string(path) + "'").c_str());
        }
    }

    BinaryFile(BinaryFile&&) = default;
    BinaryFile& operator=(BinaryFile&&) = default;
    BinaryFile(const BinaryFile&) = delete;
    BinaryFile& operator=(const BinaryFile&) = delete;

    std::vector<uint8_t> read() {
        if (!readable_) tpy_panic("read(): file not opened for reading");
        return std::vector<uint8_t>(
            std::istreambuf_iterator<char>(fs_),
            std::istreambuf_iterator<char>()
        );
    }

    int32_t write(std::span<const uint8_t> data) {
        if (!writable_) tpy_panic("write(): file not opened for writing");
        fs_.write(reinterpret_cast<const char*>(data.data()),
                  static_cast<std::streamsize>(data.size()));
        return static_cast<int32_t>(data.size());
    }

    void close() {
        if (!closed_) {
            fs_.close();
            closed_ = true;
        }
    }

    BinaryFile& __enter__() { return *this; }
    void __exit__() { close(); }

    friend std::ostream& operator<<(std::ostream& os, const BinaryFile& f) {
        return os << "<BinaryIO '" << f.path_ << "'>";
    }

    ~BinaryFile() { close(); }
};

inline TextFile builtin_open(std::string_view path) {
    return TextFile(path, "r");
}

inline TextFile builtin_open_mode(std::string_view path, std::string_view mode) {
    return TextFile(path, mode);
}

inline BinaryFile builtin_open_binary(std::string_view path, std::string_view mode) {
    return BinaryFile(path, mode);
}

} // namespace tpy
