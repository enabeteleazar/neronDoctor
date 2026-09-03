# doctor/runner.py
# Orchestrateur principal — coordonne analyse, tests, fixes, monitoring

from doctor.analyzer import analyze_project
from doctor.tester import test_services
from doctor.fixer import apply_fixes, diagnose_unhealthy
from doctor.monitor import get_system_metrics, get_all_services_status, get_all_journal_errors
from doctor.config import cfg


def run_full_diagnosis() -> dict:
    report = {
        "analysis": {},
        "monitor": {},
        "tests": {},
        "fixes": [],
        "final_status": {}
    }

    # PHASE 1 - ANALYSE STATIQUE
    report["analysis"]["core"] = analyze_project(cfg.CORE_PATH)
    report["analysis"]["llm"]  = analyze_project(cfg.LLM_PATH)

    # PHASE 2 - MONITORING SYSTÈME
    report["monitor"]["system"]   = get_system_metrics()
    report["monitor"]["services"] = get_all_services_status()
    report["monitor"]["journals"] = get_all_journal_errors()

    # PHASE 3 - TESTS RUNTIME
    report["tests"] = test_services()

    # PHASE 4 - DIAGNOSTIC (sans correction)
    # Doctor analyse, Goal repare. Cette phase appelait `apply_fixes` : le
    # timer de diagnostic redemarrait donc des services toutes les 5 min, sur
    # un seul echec de sonde. La correction est desormais reservee a un appel
    # explicite de POST /fixes.
    report["fixes"] = diagnose_unhealthy(report)

    # PHASE 5 - RE-TEST
    report["final_status"] = test_services()

    return report


# Streaming diagnosis generator (SSE)
import json
from typing import Generator

def stream_diagnosis() -> Generator[str, None, None]:
    """Génère des events Server-Sent Events (SSE) pour chaque phase.

    Chaque yield est une chaîne conforme à text/event-stream.
    """
    # start
    yield f"data: {json.dumps({'phase':'start'})}\n\n"

    # system metrics
    try:
        sys_metrics = get_system_metrics()
        yield f"data: {json.dumps({'phase':'system','data': sys_metrics})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'system','error': str(e)})}\n\n"

    # services
    try:
        services = get_all_services_status()
        yield f"data: {json.dumps({'phase':'services','data': services})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'services','error': str(e)})}\n\n"

    # journals
    try:
        journals = get_all_journal_errors()
        yield f"data: {json.dumps({'phase':'journals','data': journals})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'journals','error': str(e)})}\n\n"

    # analysis
    try:
        analysis_core = analyze_project(cfg.CORE_PATH)
        yield f"data: {json.dumps({'phase':'analysis_core','data': analysis_core})}\n\n"
        analysis_llm = analyze_project(cfg.LLM_PATH)
        yield f"data: {json.dumps({'phase':'analysis_llm','data': analysis_llm})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'analysis','error': str(e)})}\n\n"

    # tests
    try:
        tests = test_services()
        yield f"data: {json.dumps({'phase':'tests','data': tests})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'tests','error': str(e)})}\n\n"

    # fixes — diagnostic seul, comme run_full_diagnosis (Doctor ne repare pas)
    try:
        fixes = diagnose_unhealthy({'tests': tests, 'monitor': {'services': services}})
        yield f"data: {json.dumps({'phase':'fixes','data': fixes})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'fixes','error': str(e)})}\n\n"

    # final tests
    try:
        final_tests = test_services()
        yield f"data: {json.dumps({'phase':'final_tests','data': final_tests})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'final_tests','error': str(e)})}\n\n"

    # verdict
    try:
        overall_ok = True
        try:
            overall_ok = all(v.get('ok', False) for v in (final_tests or {}).values())
        except Exception:
            overall_ok = False
        verdict = {'status': 'ok' if overall_ok else 'warn'}
        yield f"data: {json.dumps({'phase':'verdict','data': verdict})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'phase':'verdict','error': str(e)})}\n\n"

    yield f"data: {json.dumps({'phase':'done'})}\n\n"
