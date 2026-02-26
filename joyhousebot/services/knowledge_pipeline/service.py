"""Knowledge Pipeline Service - standalone process for file watching and indexing."""

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from loguru import logger


def start_knowledge_pipeline_subprocess(
    workspace: str,
    source_dir: str,
    processed_dir: str,
    config: Any = None,
) -> subprocess.Popen:
    """
    Start knowledge pipeline as a subprocess.
    
    Args:
        workspace: Workspace directory path
        source_dir: Knowledge source directory path
        processed_dir: Processed files directory path
        config: Full config object
    
    Returns:
        Subprocess handle
    """
    workspace = Path(workspace).resolve()
    source_dir = Path(source_dir).resolve()
    processed_dir = Path(processed_dir).resolve()
    
    if not source_dir.exists():
        source_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created knowledge source dir: {source_dir}")
    
    if not processed_dir.exists():
        processed_dir.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        sys.executable,
        "-m",
        "joyhousebot.services.knowledge_pipeline.pipeline_worker",
    ]
    
    env = dict(os.environ)
    env["JOYHOUSEBOT_KNOWLEDGE_WORKSPACE"] = str(workspace)
    env["JOYHOUSEBOT_KNOWLEDGE_SOURCE_DIR"] = str(source_dir)
    env["JOYHOUSEBOT_KNOWLEDGE_PROCESSED_DIR"] = str(processed_dir)
    
    process = subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
    )
    
    logger.info(f"Started knowledge pipeline subprocess (PID: {process.pid}) for {source_dir}")
    return process


def stop_knowledge_pipeline_subprocess(process: subprocess.Popen) -> None:
    """Stop knowledge pipeline subprocess."""
    if process is None or process.poll() is not None:
        return
    
    logger.info(f"Stopping knowledge pipeline subprocess (PID: {process.pid})...")
    process.send_signal(signal.SIGTERM)
    
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning("Knowledge pipeline did not stop gracefully, killing...")
        process.kill()
        process.wait()
    
    logger.info("Knowledge pipeline subprocess stopped")
