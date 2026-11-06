import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def job_path(batch_dir: Path) -> Path:
    return batch_dir / "job.json"


def write_job(batch_dir: Path, **updates):
    batch_dir = Path(batch_dir)
    path = job_path(batch_dir)
    with _LOCK:
        current = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
        current.update(updates)
        current["updated_at"] = utc_now()
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        temp.replace(path)
        return current


def read_job(batch_dir: Path):
    path = job_path(Path(batch_dir))
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
