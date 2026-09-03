# app/fixer.py
# Detection des services en faute, et correction SUR DEMANDE EXPLICITE.
#
# Regle d'architecture (documentation de reference) :
#
#     Watchdog constate -> Doctor analyse -> Goal repare / construit / evolue
#
# Doctor n'est donc pas le reparateur. `diagnose_unhealthy()` est le mode
# normal : il constate et recommande, sans agir. `apply_fixes()` agit, mais
# n'est appelee que sur demande explicite (POST /fixes) — jamais par le
# diagnostic periodique.
#
# Historique : `run_full_diagnosis()` appelait `apply_fixes()` a chaque
# passage du timer (5 min). Un seul echec de sonde suffisait a declencher un
# `systemctl restart`, sans memoire d'un cycle a l'autre. Core, qui met ~25 s
# a demarrer et jusqu'a 90 s a s'arreter, etait redemarre avant d'avoir pu
# repondre : il n'a jamais atteint l'etat sain que le redemarrage cherchait a
# retablir. D'ou les garde-fous ci-dessous.

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


def _seconds_since_start(service: str) -> float | None:
    """Age du service depuis son dernier demarrage, None si indeterminable."""
    try:
        out = subprocess.check_output(
            ["systemctl", "show", service, "--property=ActiveEnterTimestampMonotonic"],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        ).strip()
    except Exception:
        return None

    _, _, value = out.partition("=")
    if not value.isdigit() or value == "0":
        return None

    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            now_us = float(handle.read().split()[0]) * 1_000_000
    except Exception:
        return None

    return max(0.0, (now_us - float(value)) / 1_000_000)


# Dernier redemarrage declenche par Doctor, par service (memoire de processus).
_last_restart: dict[str, float] = {}


def _restart_blocked_reason(service: str) -> str | None:
    """Raison de ne PAS redemarrer, ou None si le redemarrage est legitime.

    Les deux garde-fous se taisent quand l'information manque : ils bornent un
    comportement, ils ne doivent pas empecher une reparation legitime.
    """
    age = _seconds_since_start(service)
    if age is not None and age < cfg.FIX_GRACE_SECONDS:
        return (
            f"grace_period: demarre il y a {age:.0f}s "
            f"(< {cfg.FIX_GRACE_SECONDS}s) — laisser finir le demarrage"
        )

    previous = _last_restart.get(service)
    if previous is not None:
        elapsed = time.monotonic() - previous
        if elapsed < cfg.FIX_COOLDOWN_SECONDS:
            return (
                f"cooldown: deja redemarre il y a {elapsed:.0f}s "
                f"(< {cfg.FIX_COOLDOWN_SECONDS}s) — le redemarrage ne corrige rien"
            )

    return None


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


def _unhealthy_services(report: dict) -> set[str]:
    """Services juges en faute d'apres les tests HTTP et l'etat systemd."""
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

    return to_restart


def diagnose_unhealthy(report: dict) -> list[dict]:
    """Mode NORMAL de Doctor : constater et recommander, sans agir.

    C'est ce qu'appelle le diagnostic periodique. Aucun `systemctl` n'est
    execute ici : la reparation appartient a Goal.
    """
    unhealthy = _unhealthy_services(report)

    if not unhealthy:
        log.info("No services detected as unhealthy — nothing to report")
        return [{"ok": True, "message": "no_action_needed"}]

    findings: list[dict] = []
    for svc in sorted(unhealthy):
        blocked = _restart_blocked_reason(svc)
        findings.append({
            "service": svc,
            "ok": False,
            "action_taken": None,
            "recommended_action": "restart",
            "advisable_now": blocked is None,
            "note": blocked,
            "message": "diagnosed_not_repaired",
        })
        log.warning(
            "Service en faute : %s — recommandation: restart (%s)",
            svc, blocked or "applicable maintenant",
        )

    return findings


def apply_fixes(report: dict) -> list[dict]:
    """Correction SUR DEMANDE EXPLICITE (POST /fixes) — pas le mode normal.

    Conserve pour que la capacite de reparation reste disponible tant que Goal
    ne l'assume pas. Bornee par un delai de grace et un cooldown : un service
    qui demarre n'est pas redemarre, et un service deja redemarre recemment
    non plus.
    """
    fixes: list[dict] = []

    if not _systemctl_available():
        msg = "systemctl not available on this host — cannot apply fixes"
        log.error(msg)
        return [{"ok": False, "message": msg}]

    to_restart = _unhealthy_services(report)

    if not to_restart:
        log.info("No services detected as unhealthy — no fixes applied")
        return [{"ok": True, "message": "no_action_needed"}]

    for svc in sorted(to_restart):
        blocked = _restart_blocked_reason(svc)
        if blocked is not None:
            log.warning("Redemarrage de %s refuse — %s", svc, blocked)
            fixes.append({
                "service": svc,
                "ok": False,
                "action_taken": None,
                "message": f"restart_skipped: {blocked}",
            })
            continue

        _last_restart[svc] = time.monotonic()
        result = _restart_service(svc)
        result["action_taken"] = "restart"
        fixes.append(result)

    return fixes
