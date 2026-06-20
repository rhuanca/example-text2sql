from pathlib import Path

from text2sql.semantic.model import load_model

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = REPO_ROOT / "models" / "sales.yml"


def load_sales_model():
    return load_model(MODEL_PATH)
