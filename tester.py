# doctor/tester.py
# Tests runtime — vérifie la disponibilité des endpoints HTTP

import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict
from doctor.config import cfg
from doctor.logger import get_logger

log = get_logger("doctor.tester")


def _core_auth_headers() -> Dict[str, str]:
    """En-tete d'auth pour les routes protegees du Core (ex: /status).

    NERON_API_KEY est deja fourni a tous les services par
    /etc/neronOS/secrets.env via le gabarit neron@.service.
    """
    key = os.getenv("NERON_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _probe(url: str, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Effectue une requête GET et retourne une structure riche."""
    try:
        start = time.perf_counter()
        r = requests.get(url, timeout=cfg.HTTP_TIMEOUT, headers=headers or {})
        # prefer requests' elapsed if present (covers redirects), fallback to perf_counter
        latency_ms = (r.elapsed.total_seconds() * 1000.0) if getattr(r, "elapsed", None) else (time.perf_counter() - start) * 1000.0
        status = r.status_code
        ok = 200 <= status < 300
        return {
            "code": status,
            "ok": ok,
            "latency_ms": round(latency_ms, 2),
            "error": None,
        }
    except Exception as e:
        log.warning("Probe failed for %s: %s", url, e)
        return {
            "code": None,
            "ok": False,
            "latency_ms": None,
            "error": str(e),
        }


def test_services() -> Dict[str, Dict[str, Any]]:
    """Teste les endpoints configurés et renvoie une structure détaillée.

    Retourne par exemple:
    {
      "server_health": {"code":200, "ok":True, "latency_ms":12.3, "error":None},
      "llm_health":    {"code":None, "ok":False, "latency_ms":None, "error":"ConnectionError"}
    }
    """
    # Sondes paralleles plutot que sequentielles : trois requetes
    # bloquantes (requests) l'une apres l'autre pouvaient cumuler leurs
    # latences individuelles (jusqu'a plusieurs secondes chacune sous
    # charge), faisant depasser le total le timeout de 2s cote
    # doctor_snapshot() du Core -- meme cause que la latence /status deja
    # corrigee. Le temps total redevient celui de la sonde la plus lente.
    probes = {
        "server_health": (cfg.SERVER_HEALTH_URL, None),
        "server_status": (cfg.SERVER_STATUS_URL, _core_auth_headers()),
        "llm_health": (cfg.LLM_HEALTH_URL, None),
    }

    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {
            key: pool.submit(_probe, url, headers)
            for key, (url, headers) in probes.items()
        }
        for key, future in futures.items():
            results[key] = future.result()

    for key, info in results.items():
        if info.get("error"):
            log.debug("%s → %s FAILED: %s", key, getattr(cfg, key.upper() + "_URL", "<unknown>"), info["error"])
        else:
            log.debug("%s → ok %s in %sms", key, info["code"], info["latency_ms"])

    return results
