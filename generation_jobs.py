"""Single-process generation jobs and atomic publication of verified results."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from threading import Lock
import time
from uuid import uuid4

from scheduler import generate_timetable, normalize_config, verify_timetable


class GenerationBusy(Exception):
    pass


def atomic_save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".timetable-", suffix=".json", delete=False) as f:
            temporary = f.name
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


class GenerationJobs:
    def __init__(self, options=None):
        self.options = options
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timetable")
        self._jobs = {}
        self._active = None

    def submit(self, raw, output_path):
        # Validate before allocating a worker, then take an immutable snapshot.
        config = normalize_config(raw)
        with self._lock:
            if self._active is not None:
                raise GenerationBusy("A timetable is already being generated. Wait for it to finish.")
            job_id = str(uuid4())
            self._active = job_id
            self._jobs[job_id] = {
                "job_id": job_id, "status": "running", "phase": "queued",
                "outcome": None, "message": "Generation queued.", "diagnostics": [],
                "started": time.monotonic(), "finished": None,
            }
            while len(self._jobs) > 100:
                del self._jobs[next(iter(self._jobs))]
            try:
                future = self._executor.submit(self._run, job_id, config, str(output_path))
            except Exception:
                del self._jobs[job_id]
                self._active = None
                raise
        return job_id, future

    def get(self, job_id):
        with self._lock:
            job = deepcopy(self._jobs.get(job_id))
        if job is None:
            return None
        finished = job.pop("finished")
        job["elapsed_seconds"] = round((finished or time.monotonic()) - job.pop("started"), 1)
        return job

    def _update(self, job_id, **changes):
        with self._lock:
            self._jobs[job_id].update(changes)

    def _run(self, job_id, config, output_path):
        try:
            result = generate_timetable(config, self.options, lambda phase: self._update(job_id, phase=phase))
            if result.data is not None:
                self._update(job_id, phase="saving")
                result.data["metadata"].update({
                    "generation_id": job_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })
                verify_timetable(config, result.data)
                atomic_save(output_path, result.data)
                self._update(job_id, status="succeeded", outcome=result.outcome,
                             message="Complete timetable generated, verified, and saved.",
                             quality_metrics=result.data["metadata"]["quality_metrics"])
            else:
                self._update(job_id, status=result.outcome, outcome=result.outcome,
                             message=result.message, diagnostics=[result.message])
        except Exception:
            logging.exception("Timetable generation %s failed", job_id)
            self._update(job_id, status="failed", outcome="error",
                         message="Generation failed. The previous timetable was preserved. See the server log for details.")
        finally:
            with self._lock:
                self._jobs[job_id].update(phase="finished", finished=time.monotonic())
                self._active = None
        return self.get(job_id)

    def shutdown(self):
        self._executor.shutdown(wait=True)
