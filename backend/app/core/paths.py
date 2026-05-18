from pathlib import Path


GRAPH_DIR = "graph"
RUNS_DIR = "runs"
RESULTS_DIR = "results"
REPORTS_DIR = "reports"
ARTIFACTS_DIR = "artifacts"
ARTIFACT_POINTERS_DIR = "artifacts/pointers"
ARTIFACT_STORE_DIR = "artifact_store/sha256"
SCRIPTS_DIR = "scripts"
CONFIGS_DIR = "configs"
DATA_DIR = "data"


def project_root(data_root: Path, project_id: str) -> Path:
    return data_root / project_id

