"""Configuration for the warehouse backend.

Configuration is layered: built-in defaults -> YAML file -> environment
variables (BACKEND_*). The Config object can be reloaded at runtime (hot
reload) without restarting the service.

Example:
    BACKEND_DB_PATH=/data/warehouse.db BACKEND_AUTH_SECRET=... \
        python -m backend
"""

import copy
import os
from typing import Any

import yaml

_DEFAULTS = {
    "service": {
        "name": "warehouse-backend",
        "host": "0.0.0.0",
        "port": 8090,
        "version": "1.0.0",
    },
    "database": {
        "path": "./data/warehouse.db",
        "journal_mode": "WAL",
        "backup_dir": "./data/backups",
    },
    "auth": {
        "secret": None,  # REQUIRED in production (env BACKEND_AUTH_SECRET)
        "token_ttl_seconds": 86400,
        "admin_password": "admin",  # dev default; override in production
        "enabled": True,
    },
    "security": {
        "rate_limit_per_minute": 120,
        "cors_origins": ["*"],
        "secure_headers": True,
    },
    "ingest": {
        "batch_token": None,  # shared secret the ingest bridge must send
    },
    "retention": {
        "robot_positions_days": 7,
        "battery_history_days": 30,
        "events_days": 90,
        "queue_history_days": 90,
        "analytics_snapshots_days": 90,
    },
    "services": {
        "cleanup_interval_seconds": 3600,
        "archive_interval_seconds": 86400,
        "backup_interval_seconds": 86400,
        "backup_keep": 7,
        "health_monitor_interval_seconds": 5,
        "heartbeat_verification_interval_seconds": 3,
        "heartbeat_timeout_seconds": 3,
    },
    "analytics": {
        "rolling_window": 20,
        "report_hourly_minutes": 5,
    },
    "logging": {
        "level": "INFO",
        "file": "./data/logs/warehouse.log",
        "max_bytes": 5 * 1024 * 1024,
        "backup_count": 3,
    },
    "monitoring": {
        "sample_interval_seconds": 5,
        "ring_size": 120,
    },
    "dashboard": {
        "backend_url": "http://localhost:8081",
        "check_interval": 10.0,
        "allowed_origins": ["http://localhost:8081"],
    },
    "fleet": {
        "planner": {
            "score_w_distance": 1.0,
            "score_w_workload": 1.0,
            "score_w_priority": 1.0,
            "score_w_capability": 1.0,
        },
        "traffic": {
            "segment_size": 2,
            "traffic_lookahead": 1,
            "reservation_buffer": 0,
            "cell_size": 1.0,
        },
        "battery": {"low_battery_threshold": 30.0, "critical_battery_threshold": 15.0},
        "heartbeat_timeout": 3.0,
    },
}

_ENV_MAP = {
    "BACKEND_HOST": "service.host",
    "BACKEND_PORT": "service.port",
    "BACKEND_DB_PATH": "database.path",
    "BACKEND_AUTH_SECRET": "auth.secret",
    "BACKEND_TOKEN_TTL": "auth.token_ttl_seconds",
    "BACKEND_ADMIN_PASSWORD": "auth.admin_password",
    "BACKEND_AUTH_ENABLED": "auth.enabled",
    "BACKEND_RATE_LIMIT": "security.rate_limit_per_minute",
    "BACKEND_INGEST_TOKEN": "ingest.batch_token",
    "BACKEND_LOG_LEVEL": "logging.level",
    "BACKEND_LOG_FILE": "logging.file",
    "BACKEND_DASHBOARD_URL": "dashboard.backend_url",
}


class Config:
    """Hierarchical configuration with hot reload."""

    def __init__(self, path=None):
        self._path = path or os.environ.get(
            "BACKEND_CONFIG", Config._default_config_path()
        )
        self._config = copy.deepcopy(_DEFAULTS)
        self.reload()

    @staticmethod
    def _default_config_path():
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, "..", "config", "backend.yaml")

    def reload(self):
        cfg = copy.deepcopy(_DEFAULTS)
        if self._path and os.path.isfile(self._path):
            with open(self._path, "r") as fh:
                user = yaml.safe_load(fh) or {}
            _deep_merge(cfg, user)
        for env, key in _ENV_MAP.items():
            if env in os.environ:
                _set_path(cfg, key, _coerce(os.environ[env]))
        self._config = cfg
        return cfg

    def get(self, dotted, default=None) -> Any:
        node = self._config
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key) -> Any:
        return self.get(key)

    def as_dict(self):
        return copy.deepcopy(self._config)


def _coerce(value):
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _deep_merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _set_path(cfg, dotted, value):
    parts = dotted.split(".")
    node = cfg
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


# Module-level singleton (hot reloadable).
_config = Config()


def get_config():
    return _config


def reload_config():
    return _config.reload()
