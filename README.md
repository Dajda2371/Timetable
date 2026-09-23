# School timetable generator

A local Python application with a browser editor, class and teacher timetables,
and an OR-Tools CP-SAT scheduler. Requires Python 3.14 and
[uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run program.py
```

Open `http://localhost:8000` if the browser does not open automatically. Load the
example or enter teachers, classes, weekly subject hours, and school days. **Save
Config** saves the editor to `data/config.json`. **Generate Timetables** uses the
current editor values, including unsaved edits, and runs in the background.
Changes made during generation do not affect that job. Reloading the page resumes
status polling for the last job started in that browser.

## Scheduling rules

- Every class receives exactly its required weekly subject hours.
- A class has at most one lesson in each period; a teacher cannot teach two
  classes simultaneously.
- Each class/subject has one qualified teacher all week. The class teacher is
  preferred, but another qualified teacher may be selected.
- Each class gets one lunch per day from that day's **Allowed Lunch Periods**.
  Classes can take lunch at different times. An empty list reserves no lunch.
- Subject hours must be nonnegative integers. Period counts must be positive
  integers; lunch candidates must be within the day. Zero-hour subjects do not
  require a qualified teacher.

The scheduler first seeks a complete timetable, then improves these preferences
in order: student gaps, same-subject repetitions within a day, daily workload
balance, teacher gaps, and assignment to qualified class teachers. Each stage
preserves or improves the previous stages' achieved scores, even when their
optimum has not been proved. These preferences never make a valid schedule
infeasible.

A gap is an unused period between the first and last lesson. A class's lunch is
excluded from its gap count; teachers have no mandatory lunch in this version.
Daily workload imbalance is the difference between each class's busiest and
lightest configured day, summed across classes. Repetitions count lessons beyond
the first occurrence of a subject on the same day.

The default search budget is 180 seconds, plus model preparation and verification.
The first feasibility attempt gets 60 seconds. If it fails without proving
infeasibility, the rest of the budget is used for feasibility. Otherwise, remaining
time is divided among the five optimization stages. A complete feasible result is
usable even if optimal quality was not proved. A timeout without a solution is
reported separately from proven infeasibility.

Results are independently verified and atomically saved to `data/timetable.json`.
Failures preserve the previous timetable. Generated files contain the original
teacher/class maps plus `metadata`: configuration snapshot, selected class lunches,
generation ID and timestamp, solver outcome, elapsed time, seed, proven objectives,
and quality metrics. The viewer uses the snapshot so later editor changes cannot
relabel an older timetable. Older files without metadata still display, but cannot
show their originally selected lunch periods.

## HTTP interfaces

| Request | Behavior |
| --- | --- |
| `POST /generation-jobs` | Submit the configuration object directly as JSON; returns HTTP 202 with `job_id` and `status_url`. |
| `GET /generation-jobs/{job_id}` | Returns `status`, `phase`, `elapsed_seconds`, `outcome`, `message`, and `diagnostics`; successful jobs include `quality_metrics`. |
| `GET /generate` | Compatibility endpoint: generate from the saved configuration and wait for completion. |
| `GET /timetable` or `/timetables` | Read the latest successfully saved timetable. |
| `POST /save-config` | Save the editor, including drafts; scheduling validation occurs on generation. |

Job statuses are `running`, `succeeded`, `infeasible`, `timeout`, or `failed`.
Successful outcomes are `feasible` or `optimal`. Invalid generation requests return
HTTP 422 with `detail.message` and an `issues` list. Overlapping requests return
HTTP 409; missing job IDs return HTTP 404. The legacy endpoint returns HTTP 422
for infeasibility, 408 for an inconclusive timeout, and 500 for internal failures.

Run a **single server process**: there is one generation worker and the latest
100 jobs are held in memory. Restarting the server discards job history. This is a
local application; distributed jobs and multi-user deployment are outside its
scope. Rooms, teacher availability, fixed lessons, and double periods are not
modeled yet.

## Tests and benchmarks

```sh
uv run pytest -q
node --test tests/ui.test.cjs
uv run python benchmarks/benchmark_scheduler.py --classes 30
uv run python benchmarks/benchmark_scheduler.py --classes 50
```

Python tests use a fixed seed and one solver worker. Production uses a fixed seed
and up to eight workers; parallel search is not guaranteed to be reproducible.
Benchmark inputs have 25 lessons per class and twice as many teachers as classes.
Each includes an independently verified feasibility witness, but that witness is
not supplied to the solver. Reports include runtime, outcome, quality, and peak
resident memory where supported. See [benchmark results](benchmarks/RESULTS.md)
for the measured reference-machine results.
