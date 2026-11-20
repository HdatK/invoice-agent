import os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="invtest_"))
os.environ["RETRY_BACKOFF_S"] = "0"
os.environ["PROVIDER"] = "mock"
os.environ.setdefault("MOCK_TRUTH_DIR", str(ROOT / "data" / "eval_set"))
import pytest

EVAL = ROOT / "data" / "eval_set"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def truths():
    import json
    return [json.loads(p.read_text("utf-8")) for p in sorted(EVAL.glob("*.truth.json"))]
