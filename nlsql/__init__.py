"""NL-to-SQL assistant: ask questions about NYC taxi trips in plain English.

The package is split by concern into subpackages:

- :mod:`nlsql.config` -- settings (data dir, model, row cap, retries).
- :mod:`nlsql.core`   -- the pipeline around the database: data loading
  (:mod:`~nlsql.core.data`), schema introspection and the prompt
  (:mod:`~nlsql.core.schema`), the read-only guardrails
  (:mod:`~nlsql.core.guardrails`), and plain-English query summaries
  (:mod:`~nlsql.core.explain`).
- :mod:`nlsql.llm`    -- the model clients (Claude API + offline fakes).
- :mod:`nlsql.engine` -- execution with the retry loop
  (:mod:`~nlsql.engine.runner`) and the evaluation harness
  (:mod:`~nlsql.engine.evaluate`).
- :mod:`nlsql.api`    -- the FastAPI HTTP layer (:mod:`~nlsql.api.service`).
- :mod:`nlsql.cli`    -- the command-line entry point.
"""

__version__ = "0.1.0"
