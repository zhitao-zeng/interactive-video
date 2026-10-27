from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from clean_studio.domain import export_bundle_identity
from clean_studio.frozen.modelscope import (
    episode_index,
    inventory,
    single_writer,
    source,
    stage_hardlink,
)
from clean_studio.frozen.music import file_sha256
from clean_studio.store import Store


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return sha256(data).hexdigest()


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


class UploadOutcomeUncertain(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteEvidence:
    complete: bool
    target: str
    bundle_hash: str
    account: str
    private: bool
    visibility: str
    remote_object: str
    remote_sha256: str
    cover_object: str
    cover_sha256: str
    remote_index: Mapping[str, object] | None
    errors: tuple[str, ...] = ()

    @property
    def verified(self):
        return self.complete

    def __getitem__(self, key):
        return getattr(self, key)


@dataclass(frozen=True)
class UploadReceipt:
    target: str
    account: str
    episode_id: str
    input_hash: str
    video_sha256: str
    remote_object: str
    cover_object: str
    index_object: str
    video_path: str
    cover_path: str
    cover_sha256: str
    source_bytes: int
    cover_bytes: int
    index: Mapping[str, object]
    verified: bool = False
    private: bool = True
    visibility: str = "private"
    remote_index: Mapping[str, object] | None = None
    errors: tuple[str, ...] = ()

    @property
    def bundle_hash(self):
        return self.input_hash

    @property
    def remote_sha256(self):
        return self.video_sha256 if self.verified else ""

    @property
    def complete(self):
        return (
            self.verified
            and self.private is True
            and self.visibility == "private"
            and bool(self.remote_object)
            and bool(self.remote_index)
            and not self.errors
        )

    def __getitem__(self, key):
        return getattr(self, key)


class ModelScopePublisher:
    def __init__(
        self,
        *,
        store: Store,
        api=None,
        repository=None,
        work_dir,
        expected_account=None,
    ) -> None:
        if not isinstance(store, Store):
            raise TypeError("ModelScope publication requires the Clean Store acceptance query")
        self.store = store
        self._api = api
        self._repository = repository
        self.expected_account = expected_account
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        (self.work_dir / "owner").write_text(
            "Clean Studio Task7 single-writer ModelScope publication state\n",
            encoding="utf-8",
        )
        self.stage = self.work_dir / "stage"
        self.stage.mkdir(exist_ok=True)
        self.state_path = self.work_dir / "state.json"

    @property
    def api(self):
        if self._api is None:
            from clean_studio.providers.modelscope_runtime import ModelScopeWorkerAPI

            self._api = ModelScopeWorkerAPI()
        return self._api

    @property
    def repository(self):
        configured = self._repository or self.store.load()["frozen_config"].get("publish_target")
        if not isinstance(configured, str) or re.fullmatch(r"[^/]+/[^/]+", configured) is None:
            raise ValueError("a frozen owner/name ModelScope dataset target is required")
        frozen = self.store.load()["frozen_config"].get("publish_target")
        if frozen != configured:
            raise ValueError("upload target does not match frozen publish_target")
        return configured

    def _load_state(self):
        state = _read_json(self.state_path, {"uploads": {}})
        if not isinstance(state, dict) or not isinstance(state.get("uploads"), dict):
            raise ValueError("publication state is malformed")
        return state

    def _save_state(self, state):
        _write_json(self.state_path, state)

    def _current(self, accepted):
        from clean_studio.acceptance import AcceptanceError, AcceptedExport, accept_export

        if not isinstance(accepted, AcceptedExport) or accepted.status != "accepted":
            raise AcceptanceError("candidate or unaccepted export cannot be published")
        current = accept_export(
            accepted.export,
            None,
            None,
            accepted.technical,
            store=self.store,
        )
        if self.store.load()["status"] not in {"accepted", "complete"}:
            raise AcceptanceError("Clean acceptance query is not accepted for the current export")
        return current

    def _assert_current(self, current):
        from clean_studio.acceptance import AcceptanceError

        loaded = self.store.load()
        artifact_id = loaded["current_export_artifact_id"]
        artifact = next(
            (
                row
                for row in loaded["artifacts"]
                if row["id"] == artifact_id and row["kind"] == "export"
            ),
            None,
        )
        bundle = artifact["metadata"].get("export_bundle") if artifact is not None else None
        try:
            bundle_valid = (
                isinstance(bundle, Mapping)
                and bundle.get("bundle_hash") == current.bundle_hash
                and bundle.get("video_sha256") == current.export_sha256
                and export_bundle_identity(bundle) == current.bundle_hash
            )
        except ValueError:
            bundle_valid = False
        if (
            loaded["status"] not in {"accepted", "complete"}
            or artifact is None
            or artifact["sha256"] != current.export_sha256
            or not bundle_valid
        ):
            raise AcceptanceError("current accepted export bundle changed before upload")

    def _visibility(self, info):
        raw = _get(info, "visibility")
        label = _get(raw, "label") if raw is not None else None
        if not label and hasattr(raw, "name"):
            label = str(raw.name).lower()
        if isinstance(raw, str):
            label = raw.lower()
        if raw == 1:
            label = "private"
        elif raw == 5:
            label = "public"
        private_flag = _get(info, "private")
        if label == "private" and private_flag is not False:
            return True, "private"
        if label == "public" or private_flag is False:
            return False, "public"
        return False, str(label or "unknown")

    def _account(self):
        user = self.api.whoami()
        account = _get(user, "username")
        expected = self.expected_account or self.repository.split("/", 1)[0]
        if not isinstance(account, str) or account != expected:
            raise ValueError(f"unexpected ModelScope account; expected {expected}")
        return account

    def _repo_info(self):
        owner, name = self.repository.split("/", 1)
        info = self.api.get_repo(self.repository, "dataset")
        identity = _get(info, "repo_id")
        if identity is None and _get(info, "owner") and _get(info, "name"):
            identity = f"{_get(info, 'owner')}/{_get(info, 'name')}"
        if identity != self.repository:
            raise ValueError("ModelScope repository identity differs from the frozen target")
        if _get(info, "owner") != owner or _get(info, "name") != name:
            raise ValueError("ModelScope repository identity differs from the frozen target")
        return info

    def _prepare_repo(self, title):
        account = self._account()
        if not self.api.repo_exists(self.repository, "dataset"):
            self.api.create_repo(
                self.repository,
                "dataset",
                visibility="private",
                license="other",
                chinese_name=title,
                description="Clean Studio accepted episode video, cover and index.",
            )
        private, visibility = self._visibility(self._repo_info())
        if not private:
            raise ValueError(
                f"target repository must remain private; current visibility is {visibility}"
            )
        return account

    def _read_account_and_private(self):
        account = self._account()
        if not self.api.repo_exists(self.repository, "dataset"):
            return account, False, "missing"
        private, visibility = self._visibility(self._repo_info())
        return account, private, visibility

    def _download(self, path, purpose):
        target_dir = self.work_dir / purpose
        return Path(
            self.api.download_file(
                self.repository,
                "dataset",
                path,
                local_dir=target_dir,
                force=True,
            )
        )

    def _remote_matches(self, path, expected, remote):
        row = remote.get(path)
        if row is None or row["size"] != expected["bytes"]:
            return False
        remote_hash = row.get("sha256")
        if isinstance(remote_hash, str) and re.fullmatch(r"[0-9a-f]{64}", remote_hash):
            return remote_hash == expected["sha256"]
        try:
            downloaded = self._download(path, "reconcile")
        except FileNotFoundError:
            return False
        return file_sha256(downloaded) == expected["sha256"]

    def _remote_index(self, remote):
        if "episodes.json" not in remote:
            return None
        try:
            downloaded = self._download("episodes.json", "remote-index")
            value = json.loads(downloaded.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _upload_one(self, path, info, current, state, remote):
        if self._remote_matches(path, info, remote):
            state["uploads"][path] = info
            inflight = state.get("inflight")
            if (
                isinstance(inflight, Mapping)
                and inflight.get("path") == path
                and (inflight.get("source") or {}).get("sha256") == info["sha256"]
            ):
                state.pop("inflight", None)
            self._save_state(state)
            return
        saved = state["uploads"].get(path)
        if (
            isinstance(saved, Mapping)
            and saved.get("path") == info["path"]
            and saved.get("bytes") == info["bytes"]
            and saved.get("sha256") == info["sha256"]
        ):
            return
        inflight = state.get("inflight")
        if isinstance(inflight, Mapping) and inflight.get("path"):
            raise UploadOutcomeUncertain(
                "remote upload outcome is unresolved; inventory/index reconciliation is required"
            )
        self._assert_current(current)
        staged = self.stage / path
        if not staged.is_file() or file_sha256(staged) != info["sha256"]:
            raise ValueError("staged upload bytes differ from the accepted source SHA-256")
        state["inflight"] = {
            "path": path,
            "source": info,
            "remote_bytes": (remote.get(path) or {}).get("size"),
        }
        self._save_state(state)
        self.api.upload_folder(
            self.repository,
            "dataset",
            self.stage,
            allow_patterns=[path],
            commit_message="收录 Clean 当前验收导出",
            max_workers=2,
            disable_tqdm=True,
            sync_remote_repo=False,
        )
        if source(info["path"])["sha256"] != info["sha256"]:
            raise ValueError("accepted source changed during upload")
        state["uploads"][path] = info
        state.pop("inflight", None)
        self._save_state(state)

    def publish(self, accepted):
        current = self._current(accepted)
        with single_writer(self.work_dir / "publish.lock"):
            current = self._current(current)
            account = self._prepare_repo(current.title)
            state = self._load_state()
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", current.episode_id) is None:
                raise ValueError("episode identity is not safe for a remote object path")
            video_path = f"episodes/{current.episode_id}.mp4"
            cover_path = f"covers/{current.episode_id}.jpg"
            video_info = source(current.export.path)
            cover_info = source(current.export.cover_path)
            if (
                video_info["sha256"] != current.export_sha256
                or cover_info["sha256"] != current.cover_sha256
            ):
                raise ValueError("accepted source SHA-256 changed before publication")
            stage_hardlink(self.stage, video_path, current.export.path)
            stage_hardlink(self.stage, cover_path, current.export.cover_path)
            index = episode_index(
                current,
                self.repository,
                video_path,
                cover_path,
                video_info,
                cover_info,
            )
            _write_json(self.stage / "episodes.json", index)
            index_info = source(self.stage / "episodes.json")
            remote = inventory(self.api, self.repository)
            self._upload_one(video_path, video_info, current, state, remote)
            self._upload_one(cover_path, cover_info, current, state, remote)
            remote = inventory(self.api, self.repository)
            if self._remote_index(remote) != index:
                self._upload_one("episodes.json", index_info, current, state, remote)
            else:
                inflight = state.get("inflight")
                if (
                    isinstance(inflight, Mapping)
                    and inflight.get("path") == "episodes.json"
                    and (inflight.get("source") or {}).get("sha256") == index_info["sha256"]
                ):
                    state.pop("inflight", None)
                state["verified_index"] = index
                self._save_state(state)
            receipt = UploadReceipt(
                target=self.repository,
                account=account,
                episode_id=current.episode_id,
                input_hash=current.bundle_hash,
                video_sha256=current.export_sha256,
                remote_object=video_path,
                cover_object=cover_path,
                index_object="episodes.json",
                video_path=current.export.path,
                cover_path=current.export.cover_path,
                cover_sha256=current.cover_sha256,
                source_bytes=video_info["bytes"],
                cover_bytes=cover_info["bytes"],
                index=index,
            )
            evidence = self._verify_upload(receipt)
            return replace(
                receipt,
                verified=evidence.complete,
                private=evidence.private,
                visibility=evidence.visibility,
                remote_index=evidence.remote_index,
                errors=evidence.errors,
            )

    def _current_receipt_errors(self, receipt):
        errors = []
        loaded = self.store.load()
        current_id = loaded["current_export_artifact_id"]
        current = next(
            (
                row
                for row in loaded["artifacts"]
                if row["id"] == current_id and row["kind"] == "export"
            ),
            None,
        )
        bundle = current["metadata"].get("export_bundle") if current is not None else None
        try:
            bundle_valid = (
                isinstance(bundle, Mapping)
                and bundle.get("bundle_hash") == receipt.input_hash
                and bundle.get("video_sha256") == receipt.video_sha256
                and export_bundle_identity(bundle) == receipt.input_hash
            )
        except ValueError:
            bundle_valid = False
        if (
            loaded["status"] not in {"accepted", "complete"}
            or current is None
            or current["sha256"] != receipt.video_sha256
            or not bundle_valid
        ):
            errors.append("receipt is not for the current Clean accepted export bundle")
        try:
            if file_sha256(receipt.video_path) != receipt.video_sha256:
                errors.append("local video SHA-256 changed")
            if file_sha256(receipt.cover_path) != receipt.cover_sha256:
                errors.append("local cover SHA-256 changed")
        except OSError as error:
            errors.append(f"accepted source is unavailable: {error}")
        return errors

    def _verify_upload(self, receipt):
        if not isinstance(receipt, UploadReceipt):
            raise TypeError("verify_upload requires an UploadReceipt")
        if receipt.target != self.repository:
            raise ValueError("upload receipt target differs from the frozen target")
        errors = self._current_receipt_errors(receipt)
        account = receipt.account
        private = False
        visibility = "unknown"
        remote_index = None
        video_ok = False
        cover_ok = False
        try:
            account, private, visibility = self._read_account_and_private()
        except ValueError as error:
            errors.append(str(error))
        else:
            if visibility == "missing":
                errors.append("missing")
            else:
                if not private:
                    errors.append(f"target repository is not private: {visibility}")
                remote = inventory(self.api, self.repository)
                video_expected = {
                    "bytes": receipt.source_bytes,
                    "sha256": receipt.video_sha256,
                }
                cover_expected = {
                    "bytes": receipt.cover_bytes,
                    "sha256": receipt.cover_sha256,
                }
                video_ok = self._remote_matches(receipt.remote_object, video_expected, remote)
                cover_ok = self._remote_matches(receipt.cover_object, cover_expected, remote)
                if not video_ok:
                    errors.append("remote video cannot be verified against the accepted source SHA-256")
                if not cover_ok:
                    errors.append("remote cover cannot be verified against the accepted source SHA-256")
                remote_index = self._remote_index(remote)
                if remote_index != receipt.index:
                    errors.append("remote episode index differs from the target receipt")
        evidence = RemoteEvidence(
            complete=not errors,
            target=receipt.target,
            bundle_hash=receipt.input_hash,
            account=account,
            private=private,
            visibility=visibility,
            remote_object=receipt.remote_object if video_ok else "",
            remote_sha256=receipt.video_sha256 if video_ok else "",
            cover_object=receipt.cover_object if cover_ok else "",
            cover_sha256=receipt.cover_sha256 if cover_ok else "",
            remote_index=remote_index,
            errors=tuple(errors),
        )
        self._persist_verification(receipt, evidence)
        return evidence

    def verify_upload(self, receipt):
        with single_writer(self.work_dir / "publish.lock"):
            return self._verify_upload(receipt)

    def _persist_verification(self, receipt, evidence):
        observation = {
            "target": receipt.target,
            "input_hash": receipt.input_hash,
            "bundle_hash": receipt.input_hash,
            "video_sha256": receipt.video_sha256,
            "account": evidence.account,
            "private": evidence.private,
            "visibility": evidence.visibility,
            "verified": evidence.complete,
            "remote_object": evidence.remote_object if evidence.complete else "",
            "remote_sha256": evidence.remote_sha256 if evidence.complete else "",
            "cover_object": evidence.cover_object if evidence.complete else "",
            "cover_sha256": evidence.cover_sha256 if evidence.complete else "",
            "remote_index": evidence.remote_index,
            "target_index": receipt.index,
            "errors": list(evidence.errors),
        }
        verification_hash = sha256(_canonical(observation).encode("utf-8")).hexdigest()
        with self.store._db(readonly=True) as connection:
            episode_id = self.store._resolve_episode_id(connection)
            rows = connection.execute(
                """
                SELECT operations.*, artifacts.metadata AS receipt_metadata
                FROM operations
                JOIN artifacts
                  ON artifacts.operation_id = operations.id
                 AND artifacts.kind = 'receipt'
                 AND artifacts.path = operations.receipt_path
                WHERE operations.episode_id = ?
                  AND operations.kind = 'upload'
                  AND operations.input_hash = ?
                  AND operations.status = 'completed'
                ORDER BY operations.rowid DESC
                """,
                (episode_id, receipt.input_hash),
            ).fetchall()
        latest = None
        for row in rows:
            try:
                payload = json.loads(row["receipt_metadata"])
            except (TypeError, json.JSONDecodeError):
                latest = (row, None)
                break
            if payload.get("target") == receipt.target:
                latest = (row, payload)
                break
        if latest is not None:
            latest_row, latest_payload = latest
            if latest_payload is not None and all(
                latest_payload.get(key) == value for key, value in observation.items()
            ):
                return
        else:
            latest_row = None
        parent_id = latest_row["id"] if latest_row is not None else None
        key = f"upload:{receipt.target}:{receipt.input_hash}:{verification_hash}"
        if parent_id is not None:
            key += f":after:{parent_id}"
        operation = self.store.begin_operation(key, "upload", receipt.input_hash, None)
        if operation.status == "completed":
            if latest_row is not None and latest_row["id"] == operation.id:
                return
            raise ValueError("upload verification observation chain conflict")
        payload = {
            **observation,
            "receipt_id": f"receipt-{operation.id}",
            "operation_id": operation.id,
            "kind": "upload",
            "verdict": None,
            "observation_parent": parent_id,
        }
        relative = f"receipts/{operation.id}.json"
        _write_json(self.store.path.parent / relative, payload)
        self.store.complete_operation(
            operation.id,
            receipt_path=relative,
            receipt_sha256=file_sha256(self.store.path.parent / relative),
            metadata={"verified": evidence.complete, "observation_parent": parent_id},
        )
