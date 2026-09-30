"""
Startscript:  python run.py          (of: python run.py --reload tijdens ontwikkelen)

Start de webserver op http://127.0.0.1:8000 (aan te passen via .env of
CSTR_HOST / CSTR_PORT).
"""

import sys

import uvicorn

from app import config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload="--reload" in sys.argv,
        log_level=config.LOG_LEVEL,
    )
