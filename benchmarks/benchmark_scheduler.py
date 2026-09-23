"""Known-feasible larger schools; run each size in a fresh process for peak RSS."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scheduler import SolverOptions, generate_timetable, verify_timetable


def school(class_count):
    if class_count % 10:
        raise ValueError("Use a multiple of ten classes.")
    subjects = [f"Subject {i}" for i in range(10)]
    teachers_per_subject = class_count // 5
    teachers = [{"name": f"T{s}-{i}", "subjects": [subjects[s]]}
                for s in range(10) for i in range(teachers_per_subject)]
    days = {f"Day {d}": {"max_periods": 8, "lunch_breaks": [5, 6, 7]} for d in range(5)}
    config = {"teachers": teachers, "classes": [], "time_grant": {}, "schedule_config": days}
    class_map, teacher_map, lunches = {}, {t["name"]: {} for t in teachers}, {}
    for c in range(class_count):
        cn = f"Class {c}"
        config["classes"].append({"class_name": cn, "grade": str(c), "class_teacher": f"T{c % 10}-{c // 10}"})
        grant = Counter()
        class_map[cn], lunches[cn] = {}, {}
        # At each period, every group of ten classes has distinct subjects.
        # A subject's teacher stays with the same group all week.
        for d, day in enumerate(days):
            class_map[cn][day], lunches[cn][day] = {}, 5
            for offset, period in enumerate((1, 2, 3, 4, 6)):
                s = (c + d * 5 + offset) % 10
                subject, teacher = subjects[s], f"T{s}-{c // 10}"
                grant[subject] += 1
                class_map[cn][day][str(period)] = {"subject": subject, "teacher": teacher}
                teacher_map[teacher].setdefault(day, {})[str(period)] = {"subject": subject, "class": cn}
        config["time_grant"][str(c)] = dict(grant)
    witness = {"classes_timetable": class_map, "teachers_timetable": teacher_map,
               "metadata": {"class_lunches": lunches}}
    verify_timetable(config, witness)
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--classes", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    config = school(args.classes)
    started = time.monotonic()
    result = generate_timetable(config, SolverOptions(args.seconds, args.workers),
                                progress=lambda phase: print(phase, file=sys.stderr, flush=True))
    report = {"platform": platform.platform(), "python": platform.python_version(),
              "cpu_count": os.cpu_count(), "workers": args.workers, "classes": args.classes,
              "teachers": len(config["teachers"]), "required_lessons": args.classes * 25,
              "search_budget_seconds": args.seconds, "wall_seconds": round(time.monotonic() - started, 2),
              "outcome": result.outcome}
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        report["peak_rss_mib"] = round(rss / (1024 ** 2 if sys.platform == "darwin" else 1024), 1)
    except ImportError:
        report["peak_rss_mib"] = None
    if result.data:
        report["quality_metrics"] = result.data["metadata"]["quality_metrics"]
    print(json.dumps(report, indent=2), flush=True)
    return 0 if result.data else 1


if __name__ == "__main__":
    raise SystemExit(main())
