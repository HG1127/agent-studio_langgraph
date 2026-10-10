"""Run as a separate local development process: python -m app.worker.

Polls the durable SQLite queue and executes LangGraph with persistent checkpoints.
Exactly one worker is supported here; not a multi-user production runner.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from threading import Event, Thread

from . import db
from .harness.executor import process_claimed


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Studio local LangGraph worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    parser.add_argument("--poll", type=float, default=1.0, help="Polling interval in seconds")
    args = parser.parse_args()
    if not 0.2 <= args.poll <= 30:
        parser.error("--poll must be between 0.2 and 30 seconds")
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise RuntimeError("此 Worker 仅供本地开发；生产环境必须更换经过安全验收的执行服务")
    if os.getenv("STUDIO_DATABASE_BACKEND", "sqlite").lower() != "sqlite":
        raise RuntimeError(
            "PostgreSQL Worker/checkpoint adapter is not yet validated; "
            "refusing to use the SQLite worker under a PostgreSQL setting"
        )
    os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"
    logging.basicConfig(level=logging.INFO)
    db.initialize()
    identity = f"{socket.gethostname()}:{os.getpid()}"
    logging.info("LangGraph local worker started: %s", identity)
    try:
        while True:
            db.register_worker(identity)
            run_id = db.claim_next(identity)
            if run_id:
                # Keep both the worker liveness indicator and its task lease alive
                # while a slow model request is blocking the graph step.
                stopping = Event()

                def tick(active_run_id: str = run_id, stop_event: Event = stopping):
                    while not stop_event.wait(5):
                        try:
                            db.register_worker(identity)
                            db.heartbeat(active_run_id)
                        except Exception:
                            logging.exception("Worker heartbeat failed")

                heartbeat = Thread(target=tick, daemon=True)
                heartbeat.start()
                try:
                    result = process_claimed(run_id)
                    logging.info("Run %s -> %s", run_id, result["status"] if result else "missing")
                finally:
                    stopping.set()
                    heartbeat.join(timeout=2)
            elif args.once:
                break
            if args.once:
                break
            time.sleep(args.poll)
    except KeyboardInterrupt:
        logging.info("Worker interrupted: unfinished RUNNING jobs requeue when lease expires.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
