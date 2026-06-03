"""Helpers for file-like uploads in Kurigram."""

import io


def to_input_file(data: bytes, filename: str) -> io.BytesIO:
    buffer = io.BytesIO(data)
    buffer.name = filename
    buffer.seek(0)
    return buffer
