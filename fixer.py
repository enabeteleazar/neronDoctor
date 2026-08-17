# app/fixer.py
# Autocorrection améliorée : retry, validation et logs

import subprocess
import time
from typing import Any

from doctor.config import cfg
from doctor.logger import get_logger

log = get_logger("doctor.fixer")


def _systemctl_available() -> bool:
    try:
        subprocess.run(["systemctl", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


def _is_active(service: str) -> tuple[bool, str]:
    try:
        out = subprocess.check_output(["systemctl", "is-active", service], text=True, stderr=subprocess.DEVNULL).strip()
        return out == "active", out
    except subprocess.CalledProcessError as e:
        # non-zero exit -> not active
        out = e.output.strip() if getattr(e, "output", None) else ""
        return False, out
    except FileNotFoundError:
        return False, "systemctl_not_found"


def _restart_service(service: str) -> dict[str, Any]:
    last_msg = ""
    for attempt in range(1, max(1, cfg.FIX_RETRY_COUNT) + 1):
        log.info("Attempting restart (%d/%d) of %s", attempt, cfg.FIX_RETRY_COUNT, service)
        try:
            # Avoid interactive authentication prompts from polkit by refusing to ask for password.
            subprocess.run([
                "systemctl", "--no-ask-password", "restart", service
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=20)
        except FileNotFoundError:
            return {"service": service, "attempts": attempt, "ok": False, "message": "systemctl_not_found"}
        except subprocess.TimeoutExpired:
            log.warning("Restart command timed out for %s", service)
            return {"service": service, "attempts": attempt, "ok": False, "message": "restart_timeout"}
        # Wait a short delay before checking status
        time.sleep(max(1, cfg.FIX_RETRY_DELAY))
        active, out = _is_active(service)
        if active:
            log.info("Service %s is active after restart", service)
            return {"service": service, "attempts": attempt, "ok": True, "message": "restarted_and_active"}
        last_msg = out or "unknown"
        log.warning("Restart attempt %d for %s did not bring it up: %s", attempt, service, last_msg)
    return {"service": service, "attempts": cfg.FIX_RETRY_COUNT, "ok": False, "message": f"failed_after_retries: {last_msg}"}


def apply_fixes(report: dict) -> list[dict]:
    """Analyse le rapport de tests/monitor et tente de corriger les services en erreur.

    Stratégie :
    - Détecte quels services semblent KO à partir des tests HTTP et du monitoring
    - Pour chaque service détecté, tente un redémarrage avec retry
    - Vérifie l'état après chaque tentative
    - Retourne la liste des résultats détaillés
    """
    fixes: list[dict] = []

    if not _systemctl_available():
        msg = "systemctl not available on this host — cannot apply fixes"
        log.error(msg)
        return [{"ok": False, "message": msg}]

    tests = report.get("tests", {}) or {}
    monitor_services = (report.get("monitor", {}) or {}).get("services", {})

    # Mapping simple des cles de tests vers services systemd.
    # Noms alignes sur le gabarit neron@.service (unification du 28/07) —
    # "neron-server"/"neron-llm" n'existent plus depuis cette date.
    key_to_service = {
        "server_health": "neron@core",
        "server_status": "neron@core",
        "llm_health": "neron@llm",
    }

    to_restart = set()

    # 1) Basé sur les résultats des tests HTTP
    for key, val in tests.items():
        svc = key_to_service.get(key)
        if not svc:
            continue
        # Support for structured test output from tester.test_services
        if isinstance(val, dict):
            if not val.get("ok", False):
                to_restart.add(svc)
        elif isinstance(val, str):
            # legacy: error message
            to_restart.add(svc)
        elif isinstance(val, int):
            # legacy: non-2xx considéré en échec
            if val < 200 or val >= 300:
                to_restart.add(svc)

    # 2) Basé sur l'état monitor (services systemd)
    if isinstance(monitor_services, dict):
        for svc, info in monitor_services.items():
            if isinstance(info, dict) and not info.get("active", False):
                to_restart.add(svc)

    if not to_restart:
        log.info("No services detected as unhealthy — no fixes applied")
        return [{"ok": True, "message": "no_action_needed"}]

    # 3) Tenter les redémarrages
    for svc in sorted(to_restart):
        result = _restart_service(svc)
        fixes.append(result)

    return fixes
