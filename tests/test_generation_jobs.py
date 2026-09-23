from copy import deepcopy
import json
from threading import Event

from fastapi.testclient import TestClient
import pytest

import generation_jobs
from generation_jobs import GenerationBusy, GenerationJobs
import program
from scheduler import GenerationResult, SolverOptions
from test_scheduler import small_config


@pytest.fixture
def manager():
    jobs = GenerationJobs(SolverOptions(2, workers=1))
    yield jobs
    jobs.shutdown()


def test_success_publishes_verified_snapshot(manager, tmp_path):
    c = small_config()
    original = deepcopy(c)
    path = tmp_path / "new" / "timetable.json"
    job_id, future = manager.submit(c, path)
    c["classes"].clear()
    job = future.result(timeout=10)
    assert job["status"] == "succeeded"
    assert manager.get(job_id)["phase"] == "finished"
    data = json.loads(path.read_text())
    assert data["metadata"]["generation_id"] == job_id
    assert len(data["metadata"]["configuration"]["classes"]) == len(original["classes"])
    assert not list(path.parent.glob(".timetable-*"))


@pytest.mark.parametrize("outcome", ["timeout", "infeasible"])
def test_no_solution_preserves_previous_file(manager, tmp_path, monkeypatch, outcome):
    path = tmp_path / "timetable.json"
    path.write_text("previous timetable")
    monkeypatch.setattr(generation_jobs, "generate_timetable", lambda *args: GenerationResult(outcome, outcome))
    _, future = manager.submit(small_config(), path)
    assert future.result(timeout=5)["status"] == outcome
    assert path.read_text() == "previous timetable"


def test_invalid_solver_output_is_never_published(manager, tmp_path, monkeypatch):
    path = tmp_path / "timetable.json"
    path.write_text("previous timetable")
    monkeypatch.setattr(generation_jobs, "generate_timetable", lambda *args: GenerationResult("feasible", "bad", {"metadata": {}}))
    _, future = manager.submit(small_config(), path)
    assert future.result(timeout=5)["status"] == "failed"
    assert path.read_text() == "previous timetable"


def test_atomic_write_failure_preserves_previous_file(manager, tmp_path, monkeypatch):
    path = tmp_path / "timetable.json"
    path.write_text("previous timetable")

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(generation_jobs.os, "replace", fail_replace)
    _, future = manager.submit(small_config(), path)
    assert future.result(timeout=10)["status"] == "failed"
    assert path.read_text() == "previous timetable"
    assert not list(tmp_path.glob(".timetable-*"))


def test_overlapping_jobs_rejected_and_slot_released(manager, tmp_path, monkeypatch):
    started, finish = Event(), Event()

    def blocked(*args):
        started.set()
        assert finish.wait(timeout=5)
        return GenerationResult("timeout", "timeout")

    monkeypatch.setattr(generation_jobs, "generate_timetable", blocked)
    job_id, future = manager.submit(small_config(), tmp_path / "result.json")
    try:
        assert started.wait(timeout=3)
        assert manager.get(job_id)["status"] == "running"
        with pytest.raises(GenerationBusy):
            manager.submit(small_config(), tmp_path / "result.json")
    finally:
        finish.set()
    future.result(timeout=5)
    _, second = manager.submit(small_config(), tmp_path / "result.json")
    assert second.result(timeout=5)["status"] == "timeout"


@pytest.fixture
def client(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(program, "jobs", manager)
    monkeypatch.setattr(program, "TIMETABLE_FILE", str(tmp_path / "timetable.json"))
    monkeypatch.setattr(program, "CONFIG_FILE", str(tmp_path / "config.json"))
    with TestClient(program.app) as client:
        yield client


def test_http_validation_and_unknown_job(client):
    response = client.post("/generation-jobs", json=small_config(hours=10))
    assert response.status_code == 422
    assert "only" in response.json()["detail"]["issues"][0]
    assert client.get("/generation-jobs/missing").status_code == 404


def test_http_async_status_and_conflict(client, monkeypatch):
    entered, release = Event(), Event()

    def blocked(*args):
        entered.set()
        assert release.wait(timeout=5)
        return GenerationResult("timeout", "No complete timetable found.")

    monkeypatch.setattr(generation_jobs, "generate_timetable", blocked)
    response = client.post("/generation-jobs", json=small_config())
    assert response.status_code == 202
    try:
        assert entered.wait(timeout=3)
        status = client.get(response.json()["status_url"])
        assert status.json()["status"] == "running"
        assert "elapsed_seconds" in status.json()
        assert client.post("/generation-jobs", json=small_config()).status_code == 409
        assert client.get("/config").status_code == 404
    finally:
        release.set()


def test_legacy_generate_and_timetable_alias(client):
    assert client.post("/save-config", json=small_config()).status_code == 200
    response = client.get("/generate")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    first = client.get("/timetable")
    assert first.status_code == 200
    assert first.json() == client.get("/timetables").json()
    assert "configuration" in first.json()["metadata"]
