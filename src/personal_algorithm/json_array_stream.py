"""Stream top-level JSON array values with stdlib JSON decoding semantics."""

from __future__ import annotations

import codecs
import io
import json
from collections.abc import Iterator
from typing import BinaryIO, TextIO

_WHITESPACE = " \t\r\n"


def _decoded_chunks(stream: BinaryIO, chunk_size: int) -> Iterator[str]:
    buffered = io.BufferedReader(stream)
    prefix = buffered.read(4)
    decoder = codecs.getincrementaldecoder(json.detect_encoding(prefix))(errors="surrogatepass")
    yield decoder.decode(prefix, final=False)
    while data := buffered.read(chunk_size):
        yield decoder.decode(data, final=False)
    yield decoder.decode(b"", final=True)


class _ArrayScanner:
    def __init__(self) -> None:
        self.started = False
        self.finished = False
        self.quoted = False
        self.escaped = False
        self.have_value = False
        self.after_comma = False
        self.stack: list[str] = []
        self.fragments: list[str] = []

    def _delimiter(self, char: str) -> str | None:
        if self.quoted:
            if self.escaped:
                self.escaped = False
            elif char == "\\":
                self.escaped = True
            elif char == '"':
                self.quoted = False
        elif char == '"':
            self.quoted = True
        elif char in "[{":
            self.stack.append(char)
        elif char in "]}":
            if self.stack:
                if (self.stack.pop(), char) not in (("[", "]"), ("{", "}")):
                    raise ValueError("mismatched JSON delimiter")
            elif char == "}":
                raise ValueError("unexpected JSON delimiter")
            else:
                return char
        elif char == "," and not self.stack:
            return char
        return None

    def feed(self, chunk: str) -> Iterator[object]:
        start = 0
        for index, char in enumerate(chunk):
            if self.finished:
                if char not in _WHITESPACE:
                    raise ValueError("trailing data after JSON array")
                continue
            if not self.started:
                if char in _WHITESPACE:
                    start = index + 1
                    continue
                if char != "[":
                    raise ValueError("JSON value must be an array")
                self.started = True
                start = index + 1
                continue
            delimiter = self._delimiter(char)
            if delimiter is None:
                continue
            token = "".join((*self.fragments, chunk[start:index]))
            present = bool(token.strip(_WHITESPACE))
            if not present and (delimiter == "," or self.after_comma or self.have_value):
                raise ValueError("missing JSON array value")
            self.fragments.clear()
            start = index + 1
            self.finished = delimiter == "]"
            self.after_comma = delimiter == ","
            if present:
                self.have_value = True
                yield json.loads(token)
        if self.started and not self.finished:
            self.fragments.append(chunk[start:])


def iter_json_array(stream: BinaryIO, *, chunk_size: int = 65536) -> Iterator[object]:
    """Read one array value at a time; memory is bounded by the largest value.

    Individual values are decoded by ``json.loads``, retaining its handling of
    numbers, duplicate keys, and non-standard numeric constants. The caller must
    exhaust the iterator to validate the entire member, including trailing data.
    """
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    scanner = _ArrayScanner()
    for chunk in _decoded_chunks(stream, chunk_size):
        yield from scanner.feed(chunk)
    if not scanner.finished:
        raise ValueError("incomplete JSON array")


def iter_json_array_text(stream: TextIO, *, chunk_size: int = 65536) -> Iterator[object]:
    """Read one array value at a time from an already decoded text stream."""
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    scanner = _ArrayScanner()
    while chunk := stream.read(chunk_size):
        yield from scanner.feed(chunk)
    if not scanner.finished:
        raise ValueError("incomplete JSON array")
