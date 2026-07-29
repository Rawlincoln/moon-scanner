"""Deprecation headers for legacy API surface."""

from fastapi import Response

DEPRECATED_API = (
    "Prefer GET /api/moon (moon-only capital-protection feed). "
    "This endpoint is legacy and may be removed."
)


def mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sat, 01 Nov 2026 00:00:00 GMT"
    response.headers["Link"] = '</api/moon>; rel="successor-version"'
    response.headers["X-Moon-Scanner-Deprecated"] = DEPRECATED_API
