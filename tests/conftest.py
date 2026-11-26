import io

import pytest


@pytest.fixture
def short_reader():
    class ShortReader(io.RawIOBase):
        def __init__(self, data):
            self.data = io.BytesIO(data)

        def readable(self):
            return True

        def readinto(self, target):
            piece = self.data.read(min(len(target), 1))
            target[:len(piece)] = piece
            return len(piece)

    return ShortReader
