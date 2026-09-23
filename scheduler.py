"""Validated school timetabling, independent of HTTP and persistence."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import os
import time
from typing import Callable

from ortools.sat.python import cp_model


class ConfigError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(issues))


def normalize_config(raw):
    """Validate and copy input; never silently repair an invalid schedule."""
    issues = []

    def fail(message):
        issues.append(message)

    def integer(value, path, minimum=0):
        if isinstance(value, str):
            try:
                value = int(value.strip())
            except ValueError:
                pass
        if type(value) is not int or value < minimum:
            fail(f"{path} must be an integer >= {minimum}.")
            return minimum
        return value

    def name(value, path):
        if not isinstance(value, str) or not value.strip():
            fail(f"{path} must be a nonempty name.")
            return ""
        return value.strip()

    if not isinstance(raw, dict):
        raise ConfigError(["Configuration must be an object."])
    c = deepcopy(raw)
    for key, kind in (("teachers", list), ("classes", list), ("time_grant", dict), ("schedule_config", dict)):
        if not isinstance(c.get(key), kind) or not c[key]:
            fail(f"{key} must be a nonempty {kind.__name__}.")
    if issues:
        raise ConfigError(issues)

    teachers = {}
    for i, t in enumerate(c["teachers"]):
        if not isinstance(t, dict):
            fail(f"Teacher {i + 1} must be an object.")
            continue
        t["name"] = name(t.get("name"), f"Teacher {i + 1} name")
        if t["name"] in teachers:
            fail(f"Duplicate teacher: {t['name']}.")
        subjects = t.get("subjects")
        if not isinstance(subjects, list):
            fail(f"Subjects for {t['name']} must be a list.")
            subjects = []
        t["subjects"] = list(dict.fromkeys(name(s, f"Subject for {t['name']}") for s in subjects))
        teachers[t["name"]] = t["subjects"]

    grants = {}
    for grade, subjects in c["time_grant"].items():
        grade = str(grade).strip()
        if not isinstance(subjects, dict):
            fail(f"Time grant for grade {grade} must be an object.")
            continue
        normalized = {}
        for subject, hours in subjects.items():
            s = name(subject, f"Subject in grade {grade}")
            if s in normalized:
                fail(f"Duplicate subject {s} in grade {grade}.")
            normalized[s] = integer(hours, f"Hours for grade {grade}, {s}")
        if grade in grants:
            fail(f"Duplicate grade: {grade}.")
        grants[grade] = normalized
    c["time_grant"] = grants

    classes = set()
    for cls in c["classes"]:
        if not isinstance(cls, dict):
            fail("Each class must be an object.")
            continue
        cls["class_name"] = name(cls.get("class_name"), "Class name")
        cn = cls["class_name"]
        if cn in classes:
            fail(f"Duplicate class: {cn}.")
        classes.add(cn)
        grade = cls.get("grade")
        if isinstance(grade, bool) or not isinstance(grade, (str, int)):
            fail(f"Grade for {cn} must be a string or integer.")
        cls["grade"] = str(grade).strip()
        if cls["grade"] not in grants:
            fail(f"Missing time grant for class {cn}, grade {cls['grade']}.")
        cls["class_teacher"] = name(cls.get("class_teacher"), f"Class teacher for {cn}")
        if cls["class_teacher"] not in teachers:
            fail(f"Unknown class teacher {cls['class_teacher']} for {cn}.")

    days = {}
    for day, spec in c["schedule_config"].items():
        day = name(day, "Day")
        if day in days:
            fail(f"Duplicate day: {day}.")
        if not isinstance(spec, dict):
            fail(f"Schedule for {day} must be an object.")
            continue
        periods = integer(spec.get("max_periods"), f"Max periods for {day}", 1)
        lunch = spec.get("lunch_breaks", [])
        if not isinstance(lunch, list):
            fail(f"Lunch candidates for {day} must be a list.")
            lunch = []
        lunch = [integer(p, f"Lunch period for {day}", 1) for p in lunch]
        if any(p > periods + 1 for p in lunch):
            fail(f"Lunch periods for {day} must be within 1–{periods + 1} (including lunch immediately after the last lesson period).")
        days[day] = {"max_periods": periods, "lunch_breaks": sorted(set(lunch))}
    c["schedule_config"] = days
    if issues:
        raise ConfigError(issues)

    # Lunch after the final lesson period does not consume a teaching slot.
    class_capacity = sum(
        d["max_periods"] - int(bool(d["lunch_breaks"]) and d["max_periods"] + 1 not in d["lunch_breaks"])
        for d in days.values()
    )
    teacher_capacity = sum(d["max_periods"] for d in days.values())
    # Demand sharing exactly the same eligible teacher pool gives useful, sound
    # capacity diagnostics, including several subjects with one sole teacher.
    pools = defaultdict(list)
    for cls in c["classes"]:
        cn = cls["class_name"]
        grant = grants[cls["grade"]]
        if sum(grant.values()) > class_capacity:
            fail(f"Class {cn} needs {sum(grant.values())} lessons but has only {class_capacity} slots after lunch.")
        for subject, hours in grant.items():
            if not hours:
                continue
            eligible = frozenset(t for t, subjects in teachers.items() if subject in subjects)
            if not eligible:
                fail(f"No qualified teacher for {cn}: {subject} ({hours} lessons).")
            else:
                pools[eligible].append((cn, subject, hours))
    for pool, demands in pools.items():
        hours = sum(h for _, _, h in demands)
        if hours > len(pool) * teacher_capacity:
            affected = ", ".join(f"{cn}/{s}" for cn, s, _ in demands)
            fail(f"Teachers {', '.join(sorted(pool))} need {hours} lessons but have only {len(pool) * teacher_capacity} slots: {affected}.")
    total = sum(sum(grants[cls["grade"]].values()) for cls in c["classes"])
    if total > len(teachers) * teacher_capacity:
        fail(f"School needs {total} lessons but teachers have only {len(teachers) * teacher_capacity} slots.")
    if issues:
        raise ConfigError(issues)
    return c


@dataclass(frozen=True)
class SolverOptions:
    time_limit_seconds: float = 180
    workers: int = min(8, os.cpu_count() or 1)
    seed: int = 0


@dataclass
class GenerationResult:
    outcome: str
    message: str
    data: dict | None = None


METRICS = ("class_gaps", "subject_repetitions", "daily_imbalance", "teacher_gaps", "non_class_teacher_assignments")


def _gap_terms(model, occupied, exempt=None):
    """Exact gap indicators, with linear-size prefix/suffix occupancy chains."""
    n = len(occupied)
    if n < 3:
        return []
    before, after = [occupied[0]], [None] * n
    after[-1] = occupied[-1]
    for p in range(1, n):
        v = model.new_bool_var("")
        model.add_max_equality(v, [before[-1], occupied[p]])
        before.append(v)
    for p in range(n - 2, -1, -1):
        v = model.new_bool_var("")
        model.add_max_equality(v, [after[p + 1], occupied[p]])
        after[p] = v
    gaps = []
    for p in range(1, n - 1):
        gap = model.new_bool_var("")
        conditions = [before[p - 1], after[p + 1], occupied[p].Not()]
        if exempt and p + 1 in exempt:
            conditions.append(exempt[p + 1].Not())
        model.add_min_equality(gap, conditions)
        gaps.append(gap)
    return gaps


def _build_model(c):
    model = cp_model.CpModel()
    slots = [(d, p) for d, spec in c["schedule_config"].items() for p in range(1, spec["max_periods"] + 1)]
    teachers = {t["name"]: set(t["subjects"]) for t in c["teachers"]}
    class_slots, teacher_slots = defaultdict(list), defaultdict(list)
    subject_days = defaultdict(list)
    assignments, lunches, lessons = {}, {}, {}
    penalties = {key: [] for key in METRICS}

    for cls in c["classes"]:
        cn = cls["class_name"]
        for subject, hours in c["time_grant"][cls["grade"]].items():
            if not hours:
                continue
            eligible = [t for t in teachers if subject in teachers[t]]
            chosen = []
            for t in eligible:
                a = model.new_bool_var("")
                assignments[cn, subject, t] = a
                chosen.append(a)
                teacher_lessons = []
                for day, period in slots:
                    x = model.new_bool_var("")
                    lessons[cn, subject, t, day, period] = x
                    class_slots[cn, day, period].append(x)
                    teacher_slots[t, day, period].append(x)
                    subject_days[cn, subject, day].append(x)
                    teacher_lessons.append(x)
                    model.add(x <= a)
                model.add(sum(teacher_lessons) == hours * a)
            model.add_exactly_one(chosen)
            if cls["class_teacher"] in eligible:
                penalties[METRICS[4]].append(1 - assignments[cn, subject, cls["class_teacher"]])

        day_counts = []
        for day, spec in c["schedule_config"].items():
            lunch = {p: model.new_bool_var("") for p in spec["lunch_breaks"]}
            lunches[cn, day] = lunch
            if lunch:
                model.add_exactly_one(lunch.values())
            occupied = []
            for p in range(1, spec["max_periods"] + 1):
                o = model.new_bool_var("")
                model.add(o == sum(class_slots[cn, day, p]))
                if p in lunch:
                    model.add(o + lunch[p] <= 1)
                occupied.append(o)
            count = model.new_int_var(0, spec["max_periods"], "")
            model.add(count == sum(occupied))
            day_counts.append(count)
            penalties[METRICS[0]].extend(_gap_terms(model, occupied, lunch))
        max_day = max(d["max_periods"] for d in c["schedule_config"].values())
        hi, lo = model.new_int_var(0, max_day, ""), model.new_int_var(0, max_day, "")
        model.add_max_equality(hi, day_counts)
        model.add_min_equality(lo, day_counts)
        penalties[METRICS[2]].append(hi - lo)

    for variables in subject_days.values():
        repeats = model.new_int_var(0, len(slots), "")
        model.add_max_equality(repeats, [0, sum(variables) - 1])
        penalties[METRICS[1]].append(repeats)
    for t in teachers:
        for day, spec in c["schedule_config"].items():
            occupied = []
            for p in range(1, spec["max_periods"] + 1):
                o = model.new_bool_var("")
                model.add(o == sum(teacher_slots[t, day, p]))
                occupied.append(o)
            penalties[METRICS[3]].extend(_gap_terms(model, occupied))
    return model, lessons, lunches, {k: sum(v) for k, v in penalties.items()}


def generate_timetable(raw, options=None, progress: Callable[[str], None] | None = None):
    options = options or SolverOptions()
    if options.time_limit_seconds <= 0 or options.workers < 1:
        raise ValueError("Solver time limit and worker count must be positive.")
    report = progress or (lambda phase: None)
    report("validating")
    c = normalize_config(raw)
    report("building")
    started = time.monotonic()
    model, lessons, lunches, objectives = _build_model(c)
    deadline = time.monotonic() + options.time_limit_seconds
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = options.workers
    solver.parameters.random_seed = options.seed

    def solve(seconds, first=False):
        solver.parameters.max_time_in_seconds = max(0.001, seconds)
        solver.parameters.stop_after_first_solution = first
        return solver.solve(model)

    report("finding_feasible")
    status = solve(min(60, options.time_limit_seconds / 3), first=True)
    if status == cp_model.UNKNOWN and time.monotonic() < deadline:
        status = solve(deadline - time.monotonic(), first=True)
    if status == cp_model.INFEASIBLE:
        return GenerationResult("infeasible", "No complete timetable satisfies these constraints. Review teacher coverage, lesson demand, and available periods.")
    if status == cp_model.UNKNOWN:
        return GenerationResult("timeout", "The search time limit was reached without finding a complete timetable. This does not prove the configuration is impossible.")
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(f"Invalid scheduler model: {model.validate()}")

    def snapshot():
        return list(solver.response_proto.solution)

    best = snapshot()
    best_scores = {metric: int(solver.value(objective)) for metric, objective in objectives.items()}
    proven = []
    for i, (metric, objective) in enumerate(objectives.items()):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        report(f"optimizing_{metric}")
        # A solution hint is only a search aid. Bound the current objective too,
        # so an interrupted stage cannot replace our incumbent with a worse one.
        for earlier in METRICS[:i + 1]:
            model.add(objectives[earlier] <= best_scores[earlier])
        model.clear_hints()
        for index, value in enumerate(best):
            model.add_hint(model.get_int_var_from_proto_index(index), value)
        model.minimize(objective)
        status = solve(max(0.001, (deadline - time.monotonic()) / (len(objectives) - i)))
        if status == cp_model.MODEL_INVALID:
            raise RuntimeError(f"Invalid scheduler model: {model.validate()}")
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best = snapshot()
            best_scores = {key: int(solver.value(expr)) for key, expr in objectives.items()}
            if status == cp_model.OPTIMAL:
                proven.append(metric)
        # UNKNOWN retains the saved incumbent, not the failed solve response.
        # The next stage also preserves incidental improvements to earlier scores.

    data = _extract(c, lessons, lunches, best)
    data["metadata"].update({
        "solver_outcome": "optimal" if len(proven) == len(METRICS) else "feasible",
        "proven_objectives": proven,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "seed": options.seed,
        "quality_metrics": quality_metrics(c, data),
    })
    report("verifying")
    verify_timetable(c, data)
    if data["metadata"]["quality_metrics"] != best_scores:
        raise RuntimeError("Solver objective values disagree with independently computed quality metrics.")
    return GenerationResult(data["metadata"]["solver_outcome"], "Complete timetable generated and verified.", data)


def _extract(c, lessons, lunches, values):
    teacher_map = {t["name"]: {} for t in c["teachers"]}
    class_map = {cls["class_name"]: {} for cls in c["classes"]}
    for (cn, subject, t, day, p), variable in lessons.items():
        if values[variable.index]:
            class_map[cn].setdefault(day, {})[str(p)] = {"teacher": t, "subject": subject}
            teacher_map[t].setdefault(day, {})[str(p)] = {"class": cn, "subject": subject}
    selected = {cn: {} for cn in class_map}
    for (cn, day), candidates in lunches.items():
        selected[cn][day] = next((p for p, variable in candidates.items() if values[variable.index]), None)
    return {"teachers_timetable": teacher_map, "classes_timetable": class_map,
            "metadata": {"configuration": deepcopy(c), "class_lunches": selected}}


def quality_metrics(c, data):
    metrics = dict.fromkeys(METRICS, 0)
    lunches = data["metadata"]["class_lunches"]
    class_info = {cls["class_name"]: cls for cls in c["classes"]}
    teacher_subjects = {t["name"]: t["subjects"] for t in c["teachers"]}
    for kind, metric in (("classes_timetable", "class_gaps"), ("teachers_timetable", "teacher_gaps")):
        for entity, days in data[kind].items():
            counts = []
            assigned = {}
            for day in c["schedule_config"]:
                entries = days.get(day, {})
                periods = {int(p) for p in entries}
                counts.append(len(periods))
                if periods:
                    gaps = set(range(min(periods), max(periods) + 1)) - periods
                    if kind == "classes_timetable":
                        gaps.discard(lunches[entity][day])
                    metrics[metric] += len(gaps)
                if kind == "classes_timetable":
                    subjects = Counter(e["subject"] for e in entries.values())
                    metrics["subject_repetitions"] += sum(n - 1 for n in subjects.values())
                    assigned.update({e["subject"]: e["teacher"] for e in entries.values()})
            if kind == "classes_timetable":
                metrics["daily_imbalance"] += max(counts) - min(counts)
                ct = class_info[entity]["class_teacher"]
                metrics["non_class_teacher_assignments"] += sum(t != ct and s in teacher_subjects[ct] for s, t in assigned.items())
    return metrics


def verify_timetable(raw, data):
    """Check the saved representation independently of the solver formulation."""
    c = normalize_config(raw)
    teachers = {t["name"]: set(t["subjects"]) for t in c["teachers"]}
    expected_teachers = {t: {} for t in teachers}
    class_names = {cls["class_name"] for cls in c["classes"]}
    if set(data["classes_timetable"]) != class_names:
        raise ValueError("Timetable class set does not match configuration.")
    lunches = data["metadata"]["class_lunches"]
    if set(lunches) != class_names:
        raise ValueError("Lunch class set does not match configuration.")
    for cls in c["classes"]:
        cn = cls["class_name"]
        days = data["classes_timetable"][cn]
        if set(days) - set(c["schedule_config"]) or set(lunches[cn]) != set(c["schedule_config"]):
            raise ValueError(f"Invalid days for {cn}.")
        counts, assigned = Counter(), {}
        for day, spec in c["schedule_config"].items():
            lunch = lunches[cn][day]
            if (spec["lunch_breaks"] and (type(lunch) is not int or lunch not in spec["lunch_breaks"])) or (not spec["lunch_breaks"] and lunch is not None):
                raise ValueError(f"Invalid lunch for {cn} on {day}.")
            for period, entry in days.get(day, {}).items():
                p = int(period)
                if str(p) != period or not 1 <= p <= spec["max_periods"] or p == lunch:
                    raise ValueError(f"Invalid lesson period for {cn} on {day}.")
                t, s = entry["teacher"], entry["subject"]
                if t not in teachers or s not in teachers[t]:
                    raise ValueError(f"Unqualified teacher for {cn}/{s}.")
                if s in assigned and assigned[s] != t:
                    raise ValueError(f"Inconsistent teacher for {cn}/{s}.")
                assigned[s] = t
                counts[s] += 1
                teacher_day = expected_teachers[t].setdefault(day, {})
                if period in teacher_day:
                    raise ValueError(f"Teacher collision for {t} on {day}/{p}.")
                teacher_day[period] = {"class": cn, "subject": s}
        expected = Counter({s: h for s, h in c["time_grant"][cls["grade"]].items() if h})
        if counts != expected:
            raise ValueError(f"Incorrect lesson coverage for {cn}: expected {dict(expected)}, got {dict(counts)}.")
    if data["teachers_timetable"] != expected_teachers:
        raise ValueError("Teacher and class timetables disagree.")
