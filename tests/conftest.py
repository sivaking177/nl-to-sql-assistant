import pytest

from nlsql.config import DEFAULT_EVAL_FILE, Settings
from nlsql.core.data import TripDatabase
from nlsql.engine.evaluate import EvalCase, load_cases
from nlsql.engine.runner import QueryRunner
from nlsql.llm import FakeLLMClient


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest.fixture(scope="session")
def db(settings) -> TripDatabase:
    database = TripDatabase.from_settings(settings)
    yield database
    database.close()


@pytest.fixture(scope="session")
def cases() -> list[EvalCase]:
    return load_cases(DEFAULT_EVAL_FILE)


@pytest.fixture(scope="session")
def fake_client(cases) -> FakeLLMClient:
    return FakeLLMClient({c.question: c.fake_sql for c in cases})


@pytest.fixture()
def runner(db, settings) -> QueryRunner:
    return QueryRunner(db, max_rows=settings.max_rows)
