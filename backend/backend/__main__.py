"""Run the warehouse backend with uvicorn.

python -m backend
BACKEND_PORT=8091 BACKEND_DB_PATH=/data/warehouse.db python -m backend
"""

import os

import uvicorn

from .config import get_config
from .logging_config import get_logger

log = get_logger("warehouse")


def main():
    cfg = get_config()
    host = cfg.get("service.host", "0.0.0.0")
    port = int(cfg.get("service.port", 8090))
    if not cfg.get("auth.secret"):
        log.warning(
            "AUTH_SECRET not set — generating ephemeral secret "
            "(tokens invalid after restart)"
        )
        os.environ["BACKEND_AUTH_SECRET"] = os.urandom(24).hex()
        get_config().reload()
    log.info(f"starting warehouse backend on {host}:{port}")
    uvicorn.run(
        "backend.api:app", host=host, port=port, log_level="warning", access_log=False
    )


if __name__ == "__main__":
    main()
