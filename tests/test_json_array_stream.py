import io
import json

import pytest

from personal_algorithm.json_array_stream import iter_json_array


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 64])
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
def test_values_match_stdlib_across_chunk_and_encoding_boundaries(chunk_size, encoding):
    values = [
        {"title": 'quote " and slash \\ and comma, [ }', "nested": [1, {"x": "☃"}]},
        None, True, False, -12.25, 10**30, "line\nend", [], {},
    ]
    raw = json.dumps(values, ensure_ascii=False).encode(encoding)
    assert list(iter_json_array(io.BytesIO(raw), chunk_size=chunk_size)) == values


@pytest.mark.parametrize("raw", [
    b"", b"{}", b"[", b"[1,]", b"[,1]", b"[1,,2]", b"[1] trailing",
    b"[1 2]", b"[{]", b'["unterminated]', b"[true,false] []", b"[1e]",
])
def test_invalid_or_non_array_json_is_rejected(raw):
    with pytest.raises((ValueError, UnicodeError)):
        list(iter_json_array(io.BytesIO(raw), chunk_size=3))


def test_decoder_preserves_duplicate_key_and_nonstandard_number_behavior():
    raw = b'[{"x":1,"x":2,"n":NaN,"f":1e400}]'
    expected = json.loads(raw)[0]
    [actual] = list(iter_json_array(io.BytesIO(raw), chunk_size=2))
    assert actual["x"] == expected["x"] == 2
    assert str(actual["n"]) == str(expected["n"]) == "nan"
    assert actual["f"] == expected["f"] == float("inf")


def test_encoding_detection_handles_short_binary_reads():
    class ShortReader(io.RawIOBase):
        def __init__(self, data):
            self.data = io.BytesIO(data)

        def readable(self):
            return True

        def readinto(self, target):
            piece = self.data.read(min(len(target), 1))
            target[:len(piece)] = piece
            return len(piece)

    raw = '[{"title":"☃"}]'.encode("utf-16")
    assert list(iter_json_array(ShortReader(raw), chunk_size=1)) == [{"title": "☃"}]
