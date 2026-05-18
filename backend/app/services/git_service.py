from __future__ import annotations

from pathlib import Path
import subprocess


class GitService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=check,
            text=True,
            capture_output=True,
        )

    def init_repo(self) -> None:
        if not (self.root / ".git").exists():
            self._run(["init"])
        self._run(["config", "user.name", "Blueprint Manager"])
        self._run(["config", "user.email", "manager@example.local"])

    def commit(self, message: str) -> str:
        self._run(["add", "."])
        status = self._run(["status", "--porcelain"], check=False)
        if not status.stdout.strip():
            return self.head()
        self._run(["commit", "-m", message])
        return self.head()

    def log(self, limit: int = 20) -> list[dict[str, str]]:
        result = self._run(["log", f"-{limit}", "--pretty=format:%H%x09%ad%x09%s", "--date=iso-strict"], check=False)
        rows: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                rows.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
        return rows

    def head(self) -> str:
        result = self._run(["rev-parse", "HEAD"], check=False)
        return result.stdout.strip()

