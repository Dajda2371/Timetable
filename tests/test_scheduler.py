from copy import deepcopy
import json
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

import scheduler
from scheduler import (ConfigError, METRICS, SolverOptions, generate_timetable,
                       normalize_config, quality_metrics, verify_timetable)


def small_config(classes=2, teachers=2, hours=2, periods=2, lunch=None):
    return {
        "teachers": [{"name": f"T{i}", "subjects": ["Math"]} for i in range(teachers)],
        "classes": [{"class_name": f"C{i}", "grade": 1, "class_teacher": "T0"} for i in range(classes)],
        "time_grant": {"1": {"Math": hours, "Unused": 0}},
        "schedule_config": {"Mon": {"max_periods": periods, "lunch_breaks": lunch or []}},
    }


def solve(c, seconds=2):
    return generate_timetable(c, SolverOptions(seconds, workers=1))


def test_teacher_assignment_can_move_away_from_class_teacher():
    c = small_config()
    result = solve(c)
    assert result.outcome == "optimal"
    verify_timetable(c, result.data)
    assigned = {days["Mon"]["1"]["teacher"] for days in result.data["classes_timetable"].values()}
    assert assigned == {"T0", "T1"}
    assert result.data["metadata"]["quality_metrics"]["non_class_teacher_assignments"] == 1


def test_lunches_can_stagger_to_share_a_teacher():
    c = small_config(teachers=1, hours=1, lunch=[1, 2])
    result = solve(c)
    verify_timetable(c, result.data)
    assert {l["Mon"] for l in result.data["metadata"]["class_lunches"].values()} == {1, 2}


def test_infeasible_whole_subject_assignments_not_hidden_by_total_capacity():
    # Each teacher can fit one three-hour class in five periods, so two
    # teachers cannot teach three classes despite nine total hours fitting ten.
    result = solve(small_config(classes=3, teachers=2, hours=3, periods=5))
    assert result.outcome == "infeasible"
    assert result.data is None


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c["teachers"].append(deepcopy(c["teachers"][0])), "Duplicate teacher"),
    (lambda c: c["classes"].append(deepcopy(c["classes"][0])), "Duplicate class"),
    (lambda c: c["classes"][0].update(class_teacher="Missing"), "Unknown class teacher"),
    (lambda c: c["classes"][0].update(grade=99), "Missing time grant"),
    (lambda c: c["time_grant"]["1"].update(Math=-1), "integer >= 0"),
    (lambda c: c["time_grant"]["1"].update(Math=1.5), "integer >= 0"),
    (lambda c: c["time_grant"]["1"].update(Math=True), "integer >= 0"),
    (lambda c: c["time_grant"]["1"].update(Math=3), "Class C0 needs"),
    (lambda c: c["time_grant"]["1"].update(Unknown=1), "No qualified teacher"),
    (lambda c: c["schedule_config"]["Mon"].update(lunch_breaks=[3]), "within 1–2"),
    (lambda c: c["schedule_config"]["Mon"].update(max_periods=0), "integer >= 1"),
    (lambda c: c["teachers"].pop(), "Teachers T0 need"),
])
def test_invalid_config_is_actionable(mutate, match):
    c = small_config()
    mutate(c)
    with pytest.raises(ConfigError, match=match):
        normalize_config(c)


def test_numeric_strings_are_normalized_without_mutation():
    c = small_config()
    c["time_grant"]["1"]["Math"] = "2"
    c["schedule_config"]["Mon"]["max_periods"] = "2"
    normalized = normalize_config(c)
    assert normalized["time_grant"]["1"]["Math"] == 2
    assert normalized["schedule_config"]["Mon"]["max_periods"] == 2
    assert c["time_grant"]["1"]["Math"] == "2"


@pytest.mark.parametrize("raw", [None, [], {}, {"teachers": [None]}])
def test_malformed_config_has_validation_error(raw):
    with pytest.raises(ConfigError):
        normalize_config(raw)


def test_timeout_is_not_infeasibility(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args, **kwargs: cp_model.UNKNOWN)
    result = solve(small_config())
    assert result.outcome == "timeout"
    assert "does not prove" in result.message
    assert result.data is None


def test_last_complete_solution_survives_optimization_timeouts(monkeypatch):
    original = cp_model.CpSolver.solve

    def only_feasibility(self, model, *args, **kwargs):
        if not self.parameters.stop_after_first_solution:
            return cp_model.UNKNOWN
        return original(self, model, *args, **kwargs)

    monkeypatch.setattr(cp_model.CpSolver, "solve", only_feasibility)
    c = small_config()
    result = solve(c)
    assert result.outcome == "feasible"
    verify_timetable(c, result.data)
    assert result.data["metadata"]["proven_objectives"] == []


def test_quality_metrics_have_explicit_meanings():
    c = small_config(classes=1, teachers=2, hours=4, periods=5, lunch=[2])
    c["schedule_config"]["Tue"] = {"max_periods": 5, "lunch_breaks": []}
    data = {
        "classes_timetable": {"C0": {"Mon": {str(p): {"teacher": "T1", "subject": "Math"} for p in (1, 3, 5)},
                                      "Tue": {"1": {"teacher": "T1", "subject": "Math"}}}},
        "teachers_timetable": {"T0": {}, "T1": {
            "Mon": {str(p): {"class": "C0", "subject": "Math"} for p in (1, 3, 5)},
            "Tue": {"1": {"class": "C0", "subject": "Math"}}}},
        "metadata": {"class_lunches": {"C0": {"Mon": 2, "Tue": None}}},
    }
    verify_timetable(c, data)
    assert quality_metrics(c, data) == dict(zip(METRICS, [1, 2, 2, 2, 1]))


def test_optimization_spreads_subjects_balances_days_and_prefers_class_teacher():
    c = small_config(classes=1, hours=2, periods=3)
    c["schedule_config"]["Tue"] = {"max_periods": 3, "lunch_breaks": []}
    result = solve(c)
    assert result.outcome == "optimal"
    assert result.data["metadata"]["quality_metrics"] == dict.fromkeys(METRICS, 0)


def test_quality_with_multiple_subjects():
    # A single class fills three periods with two Math and one Art lesson.
    # A second day allows distributing Math without any student gaps.
    c = small_config(classes=1, hours=2, periods=3)
    c["teachers"][1]["subjects"].append("Art")
    c["time_grant"]["1"]["Art"] = 1
    c["schedule_config"]["Tue"] = {"max_periods": 3, "lunch_breaks": []}
    result = solve(c)
    metrics = result.data["metadata"]["quality_metrics"]
    assert metrics["class_gaps"] == 0
    assert metrics["subject_repetitions"] == 0
    assert metrics["daily_imbalance"] == 1


def test_optimization_stages_preserve_earlier_priorities(monkeypatch):
    build = scheduler._build_model
    original_solve = cp_model.CpSolver.solve
    objectives, scores = {}, []

    def capture_model(config):
        result = build(config)
        objectives.update(result[3])
        return result

    def capture_scores(self, model, *args, **kwargs):
        status = original_solve(self, model, *args, **kwargs)
        assert status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        scores.append([int(self.value(expr)) for expr in objectives.values()])
        return status

    monkeypatch.setattr(scheduler, "_build_model", capture_model)
    monkeypatch.setattr(cp_model.CpSolver, "solve", capture_scores)
    c = small_config(periods=4)
    c["schedule_config"]["Tue"] = {"max_periods": 4, "lunch_breaks": []}
    result = solve(c)
    assert result.outcome == "optimal"
    assert len(scores) == 6  # Feasibility, then the five ordered quality stages.
    for stage in range(5):
        assert all(scores[stage + 1][earlier] <= scores[stage][earlier] for earlier in range(stage + 1))


@pytest.mark.parametrize("corrupt", [
    lambda d: d["classes_timetable"]["C0"]["Mon"].pop("1"),
    lambda d: d["classes_timetable"]["C0"]["Mon"]["1"].update(teacher="Unknown"),
    lambda d: d["teachers_timetable"].clear(),
    lambda d: d["metadata"]["class_lunches"]["C0"].update(Mon=1),
    lambda d: d["classes_timetable"]["C0"]["Mon"].update({"99": {"teacher": "T0", "subject": "Math"}}),
])
def test_independent_verifier_rejects_corruption(corrupt):
    c = small_config()
    data = solve(c).data
    corrupt(data)
    with pytest.raises(ValueError):
        verify_timetable(c, data)


def test_example_has_all_204_lessons():
    c = json.loads((Path(__file__).resolve().parents[1] / "data/example_config.json").read_text())
    result = solve(c, seconds=15)
    assert result.data is not None, result.message
    verify_timetable(c, result.data)
    assert sum(len(periods) for days in result.data["classes_timetable"].values() for periods in days.values()) == 204


def test_repeatable_single_worker_runs():
    c = small_config()
    first, second = solve(c).data, solve(c).data
    assert first["classes_timetable"] == second["classes_timetable"]
    assert first["metadata"]["class_lunches"] == second["metadata"]["class_lunches"]
