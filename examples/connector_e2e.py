"""Run one real generated answer per connector without benchmark judges."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from tilik_agent.agent.runner import run_question

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env.local", override=True)


CASES = {
    "bps": "Berapa kepadatan penduduk DKI Jakarta pada 2021 menurut BPS?",
    "bnpb": "Berapa kejadian banjir di Indonesia pada 2024 menurut BNPB?",
    "bmkg": "Gempa terbaru yang tercatat BMKG di Indonesia",
    "bapanas": "Berapa harga cabai rawit merah hari ini di Jawa Barat menurut Bapanas?",
    "sipongi": "Berapa titik panas yang terdeteksi 24 jam terakhir di Indonesia menurut SiPongi?",
}


def main() -> None:
    selected = sys.argv[1:] or list(CASES)
    for connector in selected:
        if connector not in CASES:
            raise SystemExit(f"Unknown connector: {connector}")
        question = CASES[connector]
        print(f"Running {connector}: {question}", flush=True)
        started = time.monotonic()
        result = run_question(question, sources=[connector])
        if result.plan_error or result.sql_error or not result.answer or not result.sources:
            raise RuntimeError(f"{connector} did not produce a sourced answer: {result}")
        if connector not in result.used_connectors:
            raise RuntimeError(f"{connector} was not used")
        if connector == "bps" and not any(source["kind"] == "dataset" for source in result.sources):
            raise RuntimeError("BPS answer did not cite a dataset")
        if connector != "bps" and not any(
            source.get("provider", "").lower() == connector for source in result.sources
        ):
            raise RuntimeError(f"{connector} answer did not cite its source")
        print(json.dumps({
            "connector": connector,
            "question": question,
            "seconds": round(time.monotonic() - started, 2),
            "used_connectors": result.used_connectors,
            "source_route": result.source_route,
            "plan_error": result.plan_error,
            "sql_error": result.sql_error,
            "retrieval_error": result.retrieval_error,
            "step_status": result.step_status,
            "answer": result.answer,
            "sources": result.sources,
        }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
