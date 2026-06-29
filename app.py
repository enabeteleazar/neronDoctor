# doctor/app.py
# FastAPI entrypoint — routes modulaires + lifespan

from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends

from doctor.config import cfg
from doctor.logger import get_logger
from doctor.auth import require_api_key
from doctor.analyzer import analyze_project
from doctor.tester import test_services
from doctor.fixer import apply_fixes
from doctor.monitor import (
    get_system_metrics,
    get_all_services_status,
    get_all_journal_errors,
)
from doctor.registry_client import RegistryClient
from doctor.runner import run_full_diagnosis, stream_diagnosis
from fastapi.responses import StreamingResponse
import json

VERSION = "2.0.0"
logger = get_logger("doctor.app")

AUTH = [Depends(require_api_key)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("━━━ Neron Doctor v%s — démarrage ━━━", VERSION)
    logger.info("Config chargée depuis : %s", cfg.__class__.__module__)
    logger.info("Log dir       : %s", cfg.LOG_DIR)
    logger.info("Server health : %s", cfg.SERVER_HEALTH_URL)
    logger.info("Ollama        : %s", cfg.OLLAMA_URL)
    logger.info("Services      : %s", cfg.SYSTEMD_SERVICES)
    logger.info("Auth          : %s", "desactivee (dev)" if not cfg.API_KEY else "active")
    registry_client = RegistryClient.from_env()
    app.state.registry_client = registry_client
    await registry_client.start()
    logger.info("Doctor pret")
    try:
        yield
    finally:
        await registry_client.stop()
        logger.info("━━━ Neron Doctor — arret propre ━━━")


app = FastAPI(
    title="neronOS_DOCTOR",
    description="Neron Doctor Agent - v" + VERSION,
    version=VERSION,
    lifespan=lifespan,
)


@app.post("/diagnose", dependencies=AUTH)
def diagnose():
    """Diagnostic complet — toutes les phases."""
    return run_full_diagnosis()


@app.get("/health", dependencies=AUTH)
def health():
    """Snapshot rapide — tests HTTP uniquement."""
    return test_services()


@app.get("/monitor/system", dependencies=AUTH)
def monitor_system():
    """Metriques CPU / RAM / disque."""
    return get_system_metrics()


@app.get("/monitor/services", dependencies=AUTH)
def monitor_services():
    """Etat des services systemd."""
    return get_all_services_status()


@app.get("/monitor/journals", dependencies=AUTH)
def monitor_journals():
    """Analyse journalctl — erreurs et warnings par service."""
    return get_all_journal_errors()


@app.get("/analyze/core", dependencies=AUTH)
def analyze_core():
    """Analyse statique du projet core."""
    return analyze_project(cfg.CORE_PATH)


@app.get("/analyze/llm", dependencies=AUTH)
def analyze_llm():
    """Analyse statique du projet llm."""
    return analyze_project(cfg.LLM_PATH)


@app.get("/analyze", dependencies=AUTH)
def analyze_all():
    """Analyse statique de tous les projets."""
    return {
        "core": analyze_project(cfg.CORE_PATH),
        "llm":  analyze_project(cfg.LLM_PATH),
    }


@app.post("/fixes", dependencies=AUTH)
def fixes():
    """Teste les services et applique les corrections si necessaire."""
    tests  = test_services()
    report = {"tests": tests}
    result = apply_fixes(report)
    return {
        "tests":  tests,
        "fixes":  result,
        "status": test_services(),
    }


@app.get("/stream", dependencies=AUTH)
def stream():
    """Server-Sent Events streaming endpoint for the diagnosis pipeline."""
    # FastAPI's StreamingResponse accepts a generator yielding strings
    return StreamingResponse(stream_diagnosis(), media_type="text/event-stream")


# ── reload ───────────────────────────────────────────────────────────────

import asyncio
import threading

_reload_lock = threading.Lock()


@app.post("/reload", dependencies=AUTH)
def core_reload():
    """
    Hot-reload de la config YAML sans redémarrer le service.
    Safe mode :
      1. Lock — empêche les reloads concurrents.
      2. Nouvelle config construite avant le swap.
      3. Ancienne config remplacée atomiquement.
    """
    if not _reload_lock.acquire(blocking=False):
        return {"status": "error", "detail": "Reload already in progress"}

    try:
        from doctor.config import Config, YAML_PATH

        # 1. Construire la nouvelle config (peut lever une exception)
        new_cfg = Config(YAML_PATH)

        # 2. Swap atomique — on remplace les attributs de l'instance globale
        import doctor.config as _cfg_module
        old_cfg = _cfg_module.cfg

        for attr in vars(new_cfg):
            setattr(old_cfg, attr, getattr(new_cfg, attr))

        logger.info("Hot-reload config OK — %s", YAML_PATH)
        return {
            "status":  "ok",
            "message": "Config reloaded successfully",
            "source":  YAML_PATH,
        }

    except Exception as e:
        logger.error("Hot-reload config FAILED: %s", e)
        return {"status": "error", "detail": str(e)}

    finally:
        _reload_lock.release()
