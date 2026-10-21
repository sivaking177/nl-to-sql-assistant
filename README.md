# NL-to-SQL Assistant

Ask questions about New York taxi trips in plain English, get answers
computed from the real trip data — and a fixed evaluation set that proves
the answers are right.

## What this does, in plain words

You type a question like *"How many trips were paid for by credit card?"*
The program asks a language model (Anthropic's Claude) to turn the question
into SQL, the language databases understand. Before that SQL is allowed
anywhere near the data, a checker reads it and makes sure it can only
*read* — it cannot change or delete anything. Then the query runs against
a database of New York City taxi trips, and you get the answer as a small
table, plus a plain-English summary of what the query did.

If the model's first attempt at the SQL has a mistake in it — a misspelled
column name, say — the program shows the model the actual error message
and lets it try again, up to three attempts.

Why it matters: every team I have worked on has a queue of small data
questions that wait days for someone to write a query. The interesting
part of this project is not that a model can write SQL — it can — it's
everything around that: grounding it in the real schema so it stops
inventing column names, fencing it in so a bad query can't do damage, and
grading it against a fixed set of questions so I know it still works after
I change something. That grading is the centre of this repo, so it comes
first below.

## Worked examples

Real output, from the offline mode described under [Running it](#running-it)
(the answers below come from the built-in test client; with a live API key
the same pipeline answers questions it has never seen).

A filter question:

```
$ python -m nlsql.cli ask "How many trips were paid for by credit card?"
SQL: SELECT COUNT(*) AS card_trips FROM trips WHERE payment_type = 1
LIMIT 1000
Plan: Reads from trips; filters on payment_type = 1; aggregates with COUNT; returns at most 1000 rows.

card_trips
----------
38065
(1 row)
```

A join question — the zone names live in a second table:

```
$ python -m nlsql.cli ask "What are the top 5 drop-off zones by number of trips?"
SQL: SELECT z.Zone AS zone, COUNT(*) AS trips FROM trips t JOIN taxi_zones z ON z.LocationID = t.DOLocationID GROUP BY z.Zone ORDER BY trips DESC LIMIT 5
Plan: Reads from trips, taxi_zones; groups by z.Zone; aggregates with COUNT; orders by trips DESC; returns at most 5 rows.

zone                      | trips
--------------------------+------
Upper East Side North     | 2305
Upper East Side South     | 2109
Midtown Center            | 1822
Upper West Side South     | 1524
Times Sq/Theatre District | 1458
(5 rows)
```

A ranking question:

```
$ python -m nlsql.cli ask "Which day in January 2024 had the most trips?"
SQL: SELECT CAST(tpep_pickup_datetime AS DATE) AS day, COUNT(*) AS trips FROM trips GROUP BY day ORDER BY trips DESC LIMIT 1
Plan: Reads from trips; groups by day; aggregates with COUNT; orders by trips DESC; returns at most 1 rows.

day        | trips
-----------+------
2024-01-27 | 1812
(1 row)
```

And a failure, because the failures are the informative part. Offline,
the test client only knows the 15 evaluation questions. Ask it anything
else and it stops, plainly, instead of guessing:

```
$ python -m nlsql.cli ask "How many trips ended in Staten Island?"
error: FakeLLMClient has no canned answer for: 'How many trips ended in Staten Island?'
(exit code 2)
```

That's the fake client working as intended — it's a test double, not a
hidden demo mode — but it draws the honest line around offline mode: no
API key, no new questions. With a key, the same question goes to the live
model through the same guardrails and the same runner. Nothing in the
pipeline changes; only the client does.

## How I know it works: the evaluation set

`eval/questions.json` holds 15 fixed questions. Each has a hand-written
reference query — the ground truth — and the harness runs both the
candidate SQL and the reference through the same guardrailed runner and
compares the *result sets*, not the SQL text, because two correct queries
rarely produce byte-identical output. Each case also has property checks
(row counts, value ranges) that must hold even if both queries were wrong
in the same way, and a category tag — aggregation, filter, join, or
ranking — so the report shows which *kind* of question breaks, not just a
total. It also records how long each case took.

Latest run (offline, on my machine — `python -m nlsql.cli eval`):

| Category | Cases | Passed |
|---|---|---|
| aggregation | 4 | 4 |
| filter | 6 | 6 |
| join | 2 | 2 |
| ranking | 3 | 3 |
| **total** | **15** | **15** |

Average latency: about 4 ms per case (generation + execution) — that's
the offline client, so it measures the pipeline, not a network call.
`VALIDATION.md` has the full run, with screenshots.

Two design points matter here:

- Offline, the candidate SQL comes from a fake client whose canned
  queries are deliberately written in a different style from the
  references (different aliases, subqueries instead of literal IDs) — a
  pass means the pipeline produced the right numbers, not that two
  identical strings matched.
- The harness grades *first attempts only*. The retry loop would inflate
  the score; first-attempt accuracy is the number that tells me whether
  the prompt is any good. With `--live` the same harness grades the real
  model's answers; those results are a property of the model and will
  vary run to run.

The harness itself is tested, including sabotage cases where a wrong or
dangerous answer must make it fail. A harness that always passes would be
worth nothing.

## The guardrails, up front

Generated SQL is untrusted input, so every query passes
`validate_sql()` before it reaches the database:

- **One statement only**, and it must be a `SELECT`. Write and admin
  keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, …) are rejected.
- **The check runs on a cleaned copy** — string literals and comments
  are blanked out first, so a keyword inside a string can't cause a
  false rejection, and a keyword hidden in a comment can't slip past.
- **File-reading functions are blocked by name** (`read_parquet(...)`
  and friends), because DuckDB will happily run them inside a `SELECT`
  and that would defeat the read-only promise.
- **A row cap is enforced**: a missing `LIMIT` is appended, an oversized
  one is clamped to 1,000 rows.
- **A second layer behind the checker**: queries run against an
  in-memory database built from the bundled sample, with no credentials
  in reach and nothing to destroy. In production, against a real
  warehouse, I'd also connect read-only.

On a guardrail rejection or a database error, the interactive paths feed
the real error back to the model and let it correct itself — three
attempts in total, then the caller gets the full attempt history instead
of a bare failure. Models are good at fixing their own SQL when they can
see "column fare_amnt does not exist" and bad at guessing what went wrong
without it.

## How it works, step by step

1. **The data is loaded.** A file of real taxi trips (a sample of the New
   York City Taxi & Limousine Commission's published data for January
   2024) is loaded into a small database that lives in memory.
2. **The program reads its own database.** It looks up the actual table
   and column names, how many rows there are, and — for columns with only
   a few possible values — what those values are.
3. **That becomes the model's briefing.** The schema from step 2, plus a
   few notes a human would add ("payment code 1 means credit card"), is
   written into the instructions sent to the model with every question.
4. **The model writes SQL.** Nothing else — the instructions say one
   query, no explanation.
5. **The guardrail checks the SQL.** One statement only, reads only, no
   file-reading tricks, and a cap on how many rows it can return.
6. **The query runs.** If it fails, the error goes back to the model and
   it gets another attempt (three total). If it succeeds, the result is
   formatted as a table, with a one-sentence summary of what the query
   did.

```
   your question
        |
        v
  [ briefing built from the real schema ] ---> [ Claude writes SQL ]
                                                        |
                                                        v
                                            [ guardrail: reads only,
                                              one statement, row cap ]
                                                        |
                                          fail? --error--> back to Claude
                                                        |        (3 tries)
                                                        v
                              [ DuckDB runs it on the taxi data ]
                                                        |
                                                        v
                                   answer table + plain summary
```

## The pieces, in technical terms

The stack, with what each bit is and why it's here:

- **DuckDB** — a database that runs inside the program instead of on a
  server. I picked it because it reads the trip data file directly, needs
  no setup, and is fast enough that a 48,601-row sample feels instant. A
  warehouse (Snowflake, BigQuery) would be the wrong weight for this.
- **Parquet** — the file format the trip data ships in; a compact,
  column-oriented format that analytics tools read natively.
- **The Claude API** — Anthropic's hosted language models, called over
  HTTP. The model is swappable; only one small module talks to it.
- **FastAPI** — a Python web framework. It serves the same pipeline over
  HTTP for anything that isn't a terminal.
- **Pydantic** — a validation library. The evaluation set is a file I
  edit by hand, so each case is a pydantic model: a typo'd category fails
  the moment the file loads instead of quietly skewing the results.
- **pytest** — the test runner. **GitHub Actions** — a service that runs
  the tests automatically on every push. **Docker** — packaging so the
  API runs the same way on any machine.

The package is split into subpackages by job — the database side, the
model side, the engine that runs questions, and the HTTP layer — so a
change in one place doesn't mean reading all of it:

| Module | What it does |
|---|---|
| `nlsql/config.py` | Settings: where the data lives, which model, the row cap, how many retry attempts |
| `nlsql/core/data.py` | `TripDatabase` — loads the trip file and the zone lookup into DuckDB |
| `nlsql/core/schema.py` | `introspect_schema()` reads the live schema; `build_system_prompt()` turns it into the model's briefing |
| `nlsql/core/guardrails.py` | `validate_sql()` — the read-only checker every generated query must pass |
| `nlsql/core/explain.py` | `explain_sql()` — pulls a query apart into tables, filters, groupings, and a plain summary sentence |
| `nlsql/llm/clients.py` | The model clients: `AnthropicClient` (live), `FakeLLMClient` (canned answers, for offline work), `ScriptableFakeLLMClient` (a script of answers, for testing the retry loop) |
| `nlsql/engine/runner.py` | `QueryRunner` (validate, run, return) and `AssistingRunner` (the retry loop around it) |
| `nlsql/engine/evaluate.py` | The fixed-question test harness (above) |
| `nlsql/api/service.py` | The FastAPI app: `POST /ask`, `GET /schema`, `GET /health` |
| `nlsql/cli.py` | The command line: `info`, `schema`, `ask`, `eval` |

## The same walkthrough, with the code names

`TripDatabase.from_settings()` loads `data/yellow_tripdata_sample.parquet`
into a `trips` table and `data/taxi_zone_lookup.csv` into `taxi_zones`, on
an in-memory DuckDB connection. `introspect_schema()` queries
`information_schema` for names and types, counts rows, and enumerates the
distinct values of any column with at most 25 of them — for
`payment_type` that's 0–4, the only values this sample actually contains.
The rest of the TLC code list (5 = unknown, 6 = voided trip) never occurs
in the data; it lives only in the hand-written domain notes that
`build_system_prompt()` appends to the enumerated schema.
`AssistingRunner.ask()` sends question + briefing to the client, passes
the reply through `validate_sql()`, and executes via `QueryRunner.run()`.
On a guardrail rejection or a DuckDB error it calls the client's
`generate_correction()` with the failed SQL and the error text, and loops.
`explain_sql()` then summarises the SQL that actually ran, and
`format_result()` renders the aligned table. The test harness
(`run_evaluation()`) walks the same path once per fixed question — except
it grades the *first* answer only, on purpose (see above).

## Running it

You need Python 3.10 or newer.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m nlsql.cli info     # what's in the dataset
.venv/bin/python -m nlsql.cli schema   # print the briefing the model gets

# Offline: answers come from the fake client, which knows the 15
# evaluation questions. Enough to see the whole pipeline work.
.venv/bin/python -m nlsql.cli ask "How many trips were paid for by credit card?"
```

Live, with a key, it answers questions it has never seen (the only
configuration is two environment variables — see `.env.example`):

```bash
ANTHROPIC_API_KEY=... .venv/bin/python -m nlsql.cli ask \
    "Which borough has the most pickups, and how many?"
```

As a service instead of a CLI:

```bash
.venv/bin/uvicorn nlsql.api.service:app --port 8000
curl -X POST localhost:8000/ask -H 'Content-Type: application/json' \
     -d '{"question": "How many trips were paid for by credit card?"}'
# -> {"sql": "...", "explanation": {...}, "rows": [[38065]], "attempts": 1, ...}
```

Or with Docker (this builds the API image and serves it on port 8000):

```bash
docker compose up --build
```

## Tests

```bash
.venv/bin/python -m pytest
```

77 tests, all offline — no API key, no network. They cover the loader
against the real sample (exact row count, the real TLC column set), the
prompt's contents, one rejection test per guardrail category, the retry
loop recovering from staged failures, the explainer, the HTTP endpoints
through FastAPI's test client, the CLI's exit codes, and the full
evaluation — including sabotage tests where a wrong or dangerous answer
must make the harness fail. GitHub Actions runs the same suite, plus the
offline eval, on every push (`.github/workflows/ci.yml`). The `Makefile`
wraps the everyday commands (`make test`, `make eval`, `make lint`,
`make types` — black, ruff, and mypy are configured in `pyproject.toml`
and currently clean).

## The data

The sample is real TLC data, not generated. The full January 2024
yellow-taxi file holds 2,964,624 trips; the sample keeps every 61st trip
by pickup time — 48,601 trips spanning the whole month, about 0.9 MB.
`data/taxi_zone_lookup.csv` is the official zone lookup (265 zones).
`scripts/download_data.py` rebuilds the sample from a full monthly file
if you want a different month.

The raw feed is messy in the usual TLC ways, and I left the mess in —
the model's briefing mentions it instead of pretending the data is
clean. Counted on the sample itself:

| Quirk | Rows |
|---|---|
| Negative `fare_amount` | 610 |
| Negative `total_amount` | 572 |
| Zero or negative `trip_distance` | 980 |
| `passenger_count` of 0 | 477 |
| `passenger_count` missing (NULL) | 2,288 |
| `fare_amount` of exactly 0 | 17 |

Nothing is filtered out before loading: those rows are real records
(voided trips, meter glitches, group rides logged with no passenger
count), and whether a given question should exclude them is an
analytical decision, not something to hide at load time. The zone joins
are complete — every pickup and drop-off ID in the sample (212 and 241
distinct values) exists in the 265-row lookup; the pickup timestamps all
fall inside January 2024, and no trip ends before it starts.

## Decisions and trade-offs

- **The guardrail is a scanner, not a full SQL parser.** It blanks out
  string literals and comments, then checks what's left: one statement,
  starts with SELECT, no writing or admin keywords, no file-reading
  functions (DuckDB will happily run `read_parquet('...')` inside a
  SELECT, so those are blocked by name), and a row limit that gets added
  or clamped. That makes it small enough to audit in one read. The cost
  is conservatism — a column genuinely named `into` would be rejected. I
  accept that, and there's a second layer behind it anyway: queries run
  against an in-memory database built from the sample, with no
  credentials in reach and nothing to destroy. In production, against a
  real warehouse, I'd also connect read-only.
- **The retry loop feeds back the real error.** Models are good at
  fixing their own SQL when they can see "column fare_amnt does not
  exist" and bad at guessing what went wrong without it. Three attempts,
  then the caller gets the full attempt history instead of a bare
  failure. The evaluation harness does *not* use the loop — it grades
  first attempts, because that's the number that tells me if the prompt
  is any good.
- **The fake client is a feature.** It makes the evaluation a
  deterministic gate I can run in CI with no key and no bill. Live
  quality is the same harness with `--live`.
- **Schema in the prompt, not fine-tuning.** With two tables, the whole
  grounded schema fits in the briefing, code values included. This stops
  scaling somewhere around dozens of tables; past that I'd pick the
  relevant tables per question before generating. I haven't built that —
  this dataset doesn't need it.
- **The explainer is a reading aid, not a check.** It parses the query
  lexically, the same way the guardrail does, and summarises it. It can
  be wrong about an exotic query; it is never in the path that decides
  whether a query runs.

## Honest limitations

- Offline mode only answers the 15 evaluation questions (see the failure
  example above). That's the fake client working as intended, not a
  hidden demo mode — but it does mean you need an API key to ask anything
  new.
- The evaluation is 15 questions over one month of one dataset. It's a
  regression gate, not a benchmark score; I'd want a much larger set
  before claiming anything about general accuracy.
- The guardrail's keyword list is tuned for DuckDB's dialect.
- One database, two tables. The design generalises; the code hasn't been
  pushed past that, and I'd rather say so than imply otherwise.

## Layout

```
nlsql/
  config.py       settings
  cli.py          the command line
  core/           data.py, schema.py, guardrails.py, explain.py
  llm/            clients.py (live client + the two fakes)
  engine/         runner.py (execution + retry loop), evaluate.py
  api/            service.py (the FastAPI app)
eval/questions.json   the fixed evaluation set
data/             the trip sample + zone lookup
scripts/download_data.py   rebuilds the sample from a full TLC file
tests/            the pytest suite
Makefile          make test / eval / ask / serve / lint / types
Dockerfile, docker-compose.yml   run the API service
.github/workflows/ci.yml   tests + offline eval on every push
VALIDATION.md     the final validation runs, with screenshots
validation/       the captured output behind VALIDATION.md
```

## License

MIT — see `LICENSE`. Trip data © NYC TLC, published as open data.
