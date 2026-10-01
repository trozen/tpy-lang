/**
 * TurboPython Runtime - File I/O
 *
 * TextFile: text-mode file wrapper for the open() builtin.
 * Supports read/write/append modes, readline, readlines,
 * and context manager protocol (__enter__/__exit__).
 *
 * Interrupt check points: reads deliver a pending Ctrl-C before reading (so
 * no read data is dropped), writes after writing (so it is committed).
 */

#pragma once

#include "buffer_types.hpp"
#include "core.hpp"

#include <cerrno>
#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace tpy {

struct FileFlags {
    std::ios_base::openmode mode;
    bool readable;
    bool writable;

    static const FileFlags text_read;
    static const FileFlags binary_read;

    static FileFlags parse_text(std::string_view mode) {
        if (mode == "r" || mode == "rt")
            return {std::ios::in, true, false};
        if (mode == "w" || mode == "wt")
            return {std::ios::out | std::ios::trunc, false, true};
        if (mode == "a" || mode == "at")
            return {std::ios::out | std::ios::app, false, true};
        if (mode == "r+" || mode == "r+t" || mode == "rt+")
            return {std::ios::in | std::ios::out, true, true};
        if (mode == "w+" || mode == "w+t" || mode == "wt+")
            return {std::ios::in | std::ios::out | std::ios::trunc, true, true};
        if (mode == "a+" || mode == "a+t" || mode == "at+")
            return {std::ios::in | std::ios::out | std::ios::app, true, true};
        if (mode == "x" || mode == "xt")
            return {std::ios::out | std::ios::trunc | std::ios::noreplace, false, true};
        if (mode == "x+" || mode == "x+t" || mode == "xt+")
            return {std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace, true, true};
        raise_value_error("invalid mode: '{}'", mode);
    }

    static FileFlags parse_binary(std::string_view mode) {
        std::ios_base::openmode base = std::ios::binary;
        if (mode == "rb")
            return {base | std::ios::in, true, false};
        if (mode == "wb")
            return {base | std::ios::out | std::ios::trunc, false, true};
        if (mode == "ab")
            return {base | std::ios::out | std::ios::app, false, true};
        if (mode == "r+b" || mode == "rb+")
            return {base | std::ios::in | std::ios::out, true, true};
        if (mode == "w+b" || mode == "wb+")
            return {base | std::ios::in | std::ios::out | std::ios::trunc, true, true};
        if (mode == "a+b" || mode == "ab+")
            return {base | std::ios::in | std::ios::out | std::ios::app, true, true};
        if (mode == "xb")
            return {base | std::ios::out | std::ios::trunc | std::ios::noreplace, false, true};
        if (mode == "x+b" || mode == "xb+")
            return {base | std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace, true, true};
        raise_value_error("invalid mode: '{}'", mode);
    }
};

inline const FileFlags FileFlags::text_read = {std::ios::in, true, false};
inline const FileFlags FileFlags::binary_read = {std::ios::binary | std::ios::in, true, false};

class TextFile {
    std::fstream fs_;
    std::string path_;
    FileFlags flags_{};
    bool closed_ = false;

public:
    TextFile(std::string_view path, FileFlags flags) : path_(path), flags_(flags) {
        errno = 0;
        fs_.open(path_, flags_.mode);
        if (!fs_.is_open()) {
            // fstream doesn't expose the failure reason, but the underlying
            // open(2)'s errno survives in practice; 0 (no errno recorded) is
            // treated as ENOENT, matching the previous hardcoded
            // FileNotFoundError.
            const int err = errno != 0 ? errno : ENOENT;
            raise_mapped_os_error(err, std::generic_category().message(err), path);
        }
    }

    TextFile(TextFile&&) = default;
    TextFile& operator=(TextFile&&) = default;
    TextFile(const TextFile&) = delete;
    TextFile& operator=(const TextFile&) = delete;

    std::string read(int32_t size = -1) {
        if (!flags_.readable) raise_os_error("read(): file not opened for reading");
        check_interrupt();
        if (size < 0) {
            std::ostringstream ss;
            ss << fs_.rdbuf();
            return ss.str();
        }
        std::string buf(static_cast<size_t>(size), '\0');
        fs_.read(buf.data(), size);
        const std::streamsize got = fs_.gcount();
        buf.resize(static_cast<size_t>(got));
        // A short read hit EOF and set failbit; clear it so subsequent read()s
        // return "" (Python read-past-EOF semantics). Preserve badbit so a
        // genuine device error is not masked as clean EOF.
        if (got < size && !fs_.bad()) fs_.clear();
        return buf;
    }

    int32_t write(std::string_view text) {
        if (!flags_.writable) raise_os_error("write(): file not opened for writing");
        fs_ << text;
        check_interrupt();
        return static_cast<int32_t>(text.size());
    }

    void flush() {
        if (!flags_.writable) raise_os_error("flush(): file not opened for writing");
        fs_.flush();
        check_interrupt();
    }

    std::ostream& sink() { return fs_; }

    std::string readline() {
        if (!flags_.readable) raise_os_error("readline(): file not opened for reading");
        check_interrupt();
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
        if (!flags_.readable) raise_os_error("readlines(): file not opened for reading");
        check_interrupt();
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
    void __exit__(std::monostate, const BaseException*, std::monostate) { close(); }

    friend std::ostream& operator<<(std::ostream& os, const TextFile& f) {
        return os << "<TextIO '" << f.path_ << "'>";
    }

    ~TextFile() { close(); }
};

class BinaryFile {
    std::fstream fs_;
    std::string path_;
    FileFlags flags_{};
    bool closed_ = false;

public:
    BinaryFile(std::string_view path, FileFlags flags) : path_(path), flags_(flags) {
        errno = 0;
        fs_.open(path_, flags_.mode);
        if (!fs_.is_open()) {
            // Same errno recovery as TextFile above.
            const int err = errno != 0 ? errno : ENOENT;
            raise_mapped_os_error(err, std::generic_category().message(err), path);
        }
    }

    BinaryFile(BinaryFile&&) = default;
    BinaryFile& operator=(BinaryFile&&) = default;
    BinaryFile(const BinaryFile&) = delete;
    BinaryFile& operator=(const BinaryFile&) = delete;

    Bytes read(int32_t size = -1) {
        if (!flags_.readable) raise_os_error("read(): file not opened for reading");
        check_interrupt();
        if (size < 0) {
            return Bytes(
                std::istreambuf_iterator<char>(fs_),
                std::istreambuf_iterator<char>()
            );
        }
        Bytes buf(static_cast<size_t>(size));
        fs_.read(reinterpret_cast<char*>(buf.data()), size);
        const std::streamsize got = fs_.gcount();
        buf.resize(static_cast<size_t>(got));
        // Preserve badbit (genuine error) while clearing the EOF/failbit a
        // short read sets, so subsequent reads return b"" not a spurious error.
        if (got < size && !fs_.bad()) fs_.clear();
        return buf;
    }

    Bytes readline() {
        if (!flags_.readable) raise_os_error("readline(): file not opened for reading");
        check_interrupt();
        std::string line;
        if (!std::getline(fs_, line)) {
            return {};
        }
        Bytes result(line.begin(), line.end());
        if (!fs_.eof()) {
            result.push_back('\n');
        }
        return result;
    }

    std::vector<Bytes> readlines() {
        if (!flags_.readable) raise_os_error("readlines(): file not opened for reading");
        check_interrupt();
        std::vector<Bytes> lines;
        std::string line;
        while (std::getline(fs_, line)) {
            Bytes row(line.begin(), line.end());
            if (!fs_.eof()) row.push_back('\n');
            lines.push_back(std::move(row));
        }
        return lines;
    }

    int32_t write(std::span<const uint8_t> data) {
        if (!flags_.writable) raise_os_error("write(): file not opened for writing");
        fs_.write(reinterpret_cast<const char*>(data.data()),
                  static_cast<std::streamsize>(data.size()));
        check_interrupt();
        return static_cast<int32_t>(data.size());
    }

    void close() {
        if (!closed_) {
            fs_.close();
            closed_ = true;
        }
    }

    BinaryFile& __enter__() { return *this; }
    void __exit__(std::monostate, const BaseException*, std::monostate) { close(); }

    friend std::ostream& operator<<(std::ostream& os, const BinaryFile& f) {
        return os << "<BinaryIO '" << f.path_ << "'>";
    }

    ~BinaryFile() { close(); }
};

// --- Builtin open() helpers ---

inline TextFile builtin_open(std::string_view path) {
    return TextFile(path, FileFlags::text_read);
}

inline TextFile builtin_open_mode(std::string_view path, std::string_view mode) {
    return TextFile(path, FileFlags::parse_text(mode));
}

inline BinaryFile builtin_open_binary(std::string_view path, std::string_view mode = "rb") {
    return BinaryFile(path, FileFlags::parse_binary(mode));
}

} // namespace tpy
