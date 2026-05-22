from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import Request
from starlette.datastructures import FormData


async def read_form_data(request: Request) -> FormData:
    """Read URL-encoded forms even when python-multipart is unavailable."""
    try:
        return await request.form()
    except AssertionError as exc:
        if "python-multipart" not in str(exc):
            raise
        body = await request.body()
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        normalized = {
            key: values[-1] if len(values) == 1 else values
            for key, values in parsed.items()
        }
        return FormData(normalized)
