from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from clean_studio.providers.modelscope_worker import worker_path

DEFAULT_MODELSCOPE_PYTHON = "/mnt/disk1/zengzhitao/ms_venv/bin/python"


class ModelScopeWorkerError(RuntimeError):
    pass


class ModelScopeWorkerAPI:
    def __init__(
        self,
        python=DEFAULT_MODELSCOPE_PYTHON,
        *,
        worker=None,
        runner=None,
        timeout=3600,
    ) -> None:
        self.python = str(python)
        self.worker = Path(worker or worker_path()).resolve()
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def _call(self, method, *args, **kwargs):
        with tempfile.TemporaryDirectory(prefix="clean-modelscope-") as directory:
            request_path = Path(directory) / "request.json"
            response_path = Path(directory) / "response.json"
            request_path.write_text(
                json.dumps(
                    {
                        "action": "call",
                        "method": method,
                        "args": list(args),
                        "kwargs": kwargs,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if key.lower() not in {
                    "http_proxy", "https_proxy", "all_proxy", "ftp_proxy"
                }
            }
            done = self.runner(
                [self.python, str(self.worker), str(request_path), str(response_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
                env=environment,
            )
            if not response_path.is_file():
                raise ModelScopeWorkerError(
                    f"ModelScope worker exited {done.returncode} without a structured receipt"
                )
            try:
                receipt = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ModelScopeWorkerError("ModelScope worker returned malformed JSON") from error
            if not isinstance(receipt, dict):
                raise ModelScopeWorkerError("ModelScope worker receipt must be an object")
            if receipt.get("ok") is not True:
                error = receipt.get("error") or {}
                kind = error.get("type", "UnknownError") if isinstance(error, dict) else "UnknownError"
                known_errors = {
                    "FileNotFoundError": FileNotFoundError,
                    "ValueError": ValueError,
                    "PermissionError": PermissionError,
                    "ConnectionError": ConnectionError,
                }
                error_class = known_errors.get(kind) if isinstance(kind, str) else None
                message = f"ModelScope worker failed: {kind}"
                raise (error_class or ModelScopeWorkerError)(message)
            if "result" not in receipt:
                raise ModelScopeWorkerError("ModelScope worker receipt lacks a result")
            return receipt["result"]

    def whoami(self):
        return self._call("whoami")

    def get_repo(self, repo_id, repo_type, **kwargs):
        return self._call("get_repo", repo_id, repo_type, **kwargs)

    def repo_exists(self, repo_id, repo_type):
        return self._call("repo_exists", repo_id, repo_type)

    def create_repo(self, repo_id, repo_type, **kwargs):
        return self._call("create_repo", repo_id, repo_type, **kwargs)

    def list_repo_files(self, repo_id, repo_type, **kwargs):
        return self._call("list_repo_files", repo_id, repo_type, **kwargs)

    def upload_folder(self, repo_id, repo_type, folder_path, **kwargs):
        return self._call("upload_folder", repo_id, repo_type, str(folder_path), **kwargs)

    def download_file(self, repo_id, repo_type, file_path, **kwargs):
        kwargs = dict(kwargs)
        if "local_dir" in kwargs:
            kwargs["local_dir"] = str(kwargs["local_dir"])
        if "cache_dir" in kwargs:
            kwargs["cache_dir"] = str(kwargs["cache_dir"])
        return Path(self._call("download_file", repo_id, repo_type, file_path, **kwargs))
