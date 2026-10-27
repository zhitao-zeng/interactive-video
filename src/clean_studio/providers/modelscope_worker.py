from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
import json
import logging
from pathlib import Path
import sys

_METHODS = {
    "whoami",
    "get_repo",
    "repo_exists",
    "create_repo",
    "list_repo_files",
    "upload_folder",
    "download_file",
}


def worker_path():
    return Path(__file__).resolve()


def _default_api():
    from modelscope_hub import HubApi

    return HubApi()


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _scalar(value):
    if isinstance(value, Enum):
        label = getattr(value, "label", None)
        return label if label is not None else value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _repo(value):
    owner = _get(value, "owner")
    name = _get(value, "name")
    repo_id = _get(value, "repo_id")
    if not repo_id and owner and name:
        repo_id = f"{owner}/{name}"
    return {
        "owner": owner,
        "name": name,
        "repo_id": repo_id,
        "visibility": _scalar(_get(value, "visibility")),
        "private": _get(value, "private"),
    }


def _file(value):
    return {
        "path": _get(value, "path", ""),
        "size": int(_get(value, "size", 0) or 0),
        "sha256": _get(value, "sha256"),
        "type": _get(value, "type", "blob"),
    }


def _json_value(value):
    if isinstance(value, Enum):
        return _scalar(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _error(error):
    result = {"type": type(error).__name__}
    if isinstance(error, ModuleNotFoundError):
        result["module"] = error.name
    return result


def handle_request(request, *, api=None):
    try:
        if not isinstance(request, Mapping):
            raise ValueError("ModelScope worker request must be a JSON object")
        action = request.get("action")
        if action == "construct":
            if api is None:
                api = _default_api()
            return {
                "ok": True,
                "result": {"backend": "modelscope_hub", "constructed": True},
            }
        if action != "call":
            raise ValueError("ModelScope worker action must be call or construct")
        method = request.get("method")
        if method not in _METHODS:
            raise ValueError("ModelScope worker method is not allowed")
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, Mapping):
            raise ValueError("ModelScope worker args/kwargs are malformed")
        if api is None:
            api = _default_api()
        result = getattr(api, method)(*args, **dict(kwargs))
        if method == "whoami":
            result = {"username": _get(result, "username")}
        elif method in {"get_repo", "create_repo"}:
            result = _repo(result)
        elif method == "repo_exists":
            result = bool(result)
        elif method == "list_repo_files":
            result = [_file(row) for row in result]
        elif method == "download_file":
            result = str(result)
        else:
            result = _json_value(result)
        return {"ok": True, "result": result}
    except Exception as error:
        return {"ok": False, "error": _error(error)}


def main(argv=None):
    logging.disable(logging.CRITICAL)
    arguments = list(argv or sys.argv[1:])
    if len(arguments) != 2:
        raise SystemExit(2)
    request_path, response_path = map(Path, arguments)
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as error:
        receipt = {"ok": False, "error": _error(error)}
    else:
        receipt = handle_request(request)
    response_path.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
