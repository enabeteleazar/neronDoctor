# app/config.py
# Configuration chargée depuis /etc/neron/neron.yaml (section "doctor:")
# Fallback sur des valeurs par défaut si la clé est absente.

import os
import yaml
from typing import Any

YAML_PATH = os.getenv("NERON_CONFIG", "/etc/neron/neron.yaml")


def _load_yaml(path: str) -> dict[str, Any]:
    """Charge le fichier YAML global Néron et retourne la section 'doctor'.

    Si le fichier est absent ou invalide, retourne un dictionnaire vide
    pour permettre de démarrer avec les valeurs par défaut plutôt que
    de lever une exception (utile en dev/local).
    """
    if not os.path.exists(path):
        # Ne pas planter l'application si le fichier de config est absent.
        # Émettre un avertissement (RuntimeWarning) et retourner les valeurs par défaut.
        import warnings

        warnings.warn(
            f"Neron config not found: {path}. Using defaults.", RuntimeWarning
        )
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            full = yaml.safe_load(f) or {}
    except Exception as e:  # yaml parsing errors, IO errors, etc.
        import warnings

        warnings.warn(
            f"Failed to load Neron config {path}: {e}. Using defaults.", RuntimeWarning
        )
        full = {}

    return full.get("doctor", {})


def _get(d: dict, key: str, default: Any) -> Any:
    """Lecture avec fallback typé."""
    val = d.get(key, default)
    if val is None:
        return default
    if isinstance(default, bool):
        return bool(val)
    if isinstance(default, float):
        return float(val)
    if isinstance(default, int):
        return int(val)
    return val


class Config:
    def __init__(self, yaml_path: str = YAML_PATH):
        d = _load_yaml(yaml_path)

        # ── Chemins ──────────────────────────────────────────
        paths = d.get("paths", {})
        self.CORE_PATH: str = paths.get("core", "/etc/neron/core")
        self.SERVER_PATH: str = paths.get("server", "/etc/neron/server")
        self.LLM_PATH:    str = paths.get("llm",    "/etc/neron/llm")
        self.LOG_DIR:     str = paths.get("logs",   "/var/log/neron")

        # ── Endpoints HTTP ───────────────────────────────────
        endpoints = d.get("endpoints", {})
        self.SERVER_HEALTH_URL: str = endpoints.get("server_health", "http://localhost:8010/health")
        self.SERVER_STATUS_URL: str = endpoints.get("server_status", "http://localhost:8010/status")
        self.LLM_HEALTH_URL:    str = endpoints.get("llm_health",    "http://localhost:8765/llm/health")
        self.OLLAMA_URL:        str = endpoints.get("ollama",         "http://localhost:11434/api/tags")

        # ── Services systemd ──────────────────────────────────
        self.SYSTEMD_SERVICES: list[str] = d.get(
            "services", ["neron-server", "neron-llm", "ollama"]
        )

        # ── Auth ─────────────────────────────────────────────
        self.API_KEY: str = d.get("api_key", "")
        self.AUTH_DEV_MODE: bool = (
            os.getenv("NERON_DOCTOR_AUTH_DEV_MODE", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )

        # ── Timeouts & retry ──────────────────────────────────
        timing = d.get("timing", {})
        self.HTTP_TIMEOUT:    int = _get(timing, "http_timeout",    5)
        self.FIX_RETRY_COUNT: int = _get(timing, "fix_retry_count", 3)
        self.FIX_RETRY_DELAY: int = _get(timing, "fix_retry_delay", 4)
        self.JOURNAL_LINES:   int = _get(timing, "journal_lines",   100)

        # ── Seuils d'alerte système ───────────────────────────
        thresholds = d.get("thresholds", {})
        self.CPU_WARN_PERCENT:  float = _get(thresholds, "cpu",  80.0)
        self.MEM_WARN_PERCENT:  float = _get(thresholds, "mem",  85.0)
        self.DISK_WARN_PERCENT: float = _get(thresholds, "disk", 90.0)


cfg = Config()
