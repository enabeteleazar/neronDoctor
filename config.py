# app/config.py
# Configuration chargée depuis NERON_CONFIG (section "doctor:")
# Fallback sur des valeurs par défaut si la clé est absente.

import os
import yaml
from typing import Any

from common.paths import NERON_CONFIG, NERON_SERVER_DIR

YAML_PATH = str(NERON_CONFIG)


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
        self.CORE_PATH: str = paths.get("core", str(NERON_SERVER_DIR / "core"))
        self.SERVER_PATH: str = paths.get("server", str(NERON_SERVER_DIR))
        self.LLM_PATH:    str = paths.get("llm", str(NERON_SERVER_DIR / "llm"))
        self.LOG_DIR:     str = paths.get("logs",   "/var/log/neron")

        # ── Endpoints HTTP ───────────────────────────────────
        endpoints = d.get("endpoints", {})
        self.SERVER_HEALTH_URL: str = endpoints.get("server_health", "http://localhost:8010/health")
        self.SERVER_STATUS_URL: str = endpoints.get("server_status", "http://localhost:8010/status")
        # Adresse reelle du service llm (127.0.1.2), pas "localhost" — voir
        # neron.server.yaml, noeud "llm". Localhost ne route pas vers les
        # adresses de loopback dediees par service.
        self.LLM_HEALTH_URL:    str = endpoints.get("llm_health",    "http://127.0.1.2:8765/llm/health")
        self.OLLAMA_URL:        str = endpoints.get("ollama",         "http://localhost:11434/api/tags")

        # ── Services systemd ──────────────────────────────────
        # Noms alignes sur le gabarit neron@.service (unification du 28/07) :
        # les cinq services applicatifs + ollama (unite hors gabarit, gere a part).
        self.SYSTEMD_SERVICES: list[str] = d.get(
            "services",
            [
                "neron@core",
                "neron@llm",
                "neron@goal",
                "neron@memory",
                "neron@voice",
                "ollama",
            ],
        )

        # ── Auth ─────────────────────────────────────────────
        # Cle lue en priorite depuis l'environnement (secrets.env), jamais
        # en clair dans neron.yaml qui est explicitement documente "sans
        # secret". Repli sur l'ancien champ YAML pour compatibilite.
        self.API_KEY: str = os.getenv("NERON_DOCTOR_API_KEY", "") or d.get("api_key", "")
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
