"""Moon Scanner entrypoint — thin wrapper for uvicorn / gunicorn.

Deploy and local start keep using ``main:app``.
Factory: ``from app import create_app``.
"""

from __future__ import annotations

import os

from config import IS_PRODUCTION

from app import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=not IS_PRODUCTION)
