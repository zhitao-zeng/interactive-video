from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import re
import shlex


def load_dotenv(
    path: str | Path,
    *,
    environment: MutableMapping[str, str] | None = None,
) -> None:
    target = os.environ if environment is None else environment
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in {"'", '"'}:
            tokens = shlex.split(value, comments=True)
            value = tokens[0] if tokens else ""
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        target.setdefault(key, value)
