from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect


BACKUP_FORMAT_VERSION = "duckdb_export_v1"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sql_string(value: Path) -> str:
    return "'" + str(value.resolve()).replace("'", "''") + "'"


class BackupService:
    def __init__(self, db_path: Path, backup_dir: Path) -> None:
        self.db_path = db_path
        self.backup_dir = backup_dir

    def create(self, *, created_by: str) -> dict[str, Any]:
        if not self.db_path.exists():
            raise ValueError("Database does not exist")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        created_at = _utc_now()
        backup_id = (
            f"backup_{created_at.strftime('%Y%m%dT%H%M%SZ')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        archive_path = self.backup_dir / f"{backup_id}.zip"
        try:
            with tempfile.TemporaryDirectory(
                prefix=f".{backup_id}_",
                dir=self.backup_dir,
            ) as temporary:
                export_dir = Path(temporary) / "database"
                table_counts, duckdb_version = self._export(export_dir)
                exported_files = self._file_manifest(export_dir)
                manifest = {
                    "format_version": BACKUP_FORMAT_VERSION,
                    "backup_id": backup_id,
                    "created_at": created_at.isoformat(),
                    "created_by": created_by,
                    "source_database": self.db_path.name,
                    "duckdb_version": duckdb_version,
                    "table_counts": table_counts,
                    "files": exported_files,
                }
                (export_dir / "manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                self._write_archive(export_dir, archive_path)
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise
        verification = self.verify(
            backup_id,
            verified_by=created_by,
        )
        return {
            **manifest,
            "archive_path": str(archive_path.resolve()),
            "archive_size_bytes": archive_path.stat().st_size,
            "archive_sha256": _sha256(archive_path),
            "verified": verification["verified"],
            "verified_table_count": verification["table_count"],
            "verified_row_count": verification["row_count"],
        }

    def list(self) -> dict[str, Any]:
        if not self.backup_dir.exists():
            return {"backups": []}
        backups = []
        for path in sorted(
            self.backup_dir.glob("backup_*.zip"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                manifest = self._read_manifest(path)
            except (ValueError, zipfile.BadZipFile, OSError):
                backups.append(
                    {
                        "backup_id": path.stem,
                        "archive_path": str(path.resolve()),
                        "status": "invalid",
                    }
                )
                continue
            backups.append(
                {
                    **manifest,
                    "archive_path": str(path.resolve()),
                    "archive_size_bytes": path.stat().st_size,
                    "archive_sha256": _sha256(path),
                    "status": "available",
                }
            )
        return {"backups": backups}

    def verify(
        self,
        backup_id: str,
        *,
        verified_by: str = "system",
    ) -> dict[str, Any]:
        archive_path = self._archive_path(backup_id)
        manifest = self._read_manifest(archive_path)
        if manifest.get("backup_id") != backup_id:
            raise ValueError("Backup ID does not match its manifest")
        with tempfile.TemporaryDirectory(prefix=".backup_verify_") as temporary:
            export_dir = Path(temporary) / "database"
            self._safe_extract(archive_path, export_dir)
            self._verify_export_files(export_dir, manifest)
            restored_path = Path(temporary) / "verified.duckdb"
            actual_counts = self._import_and_count(export_dir, restored_path)
        expected_counts = manifest["table_counts"]
        if actual_counts != expected_counts:
            raise ValueError("Backup table counts do not match the manifest")
        result = {
            "backup_id": backup_id,
            "verified": True,
            "archive_sha256": _sha256(archive_path),
            "table_count": len(actual_counts),
            "row_count": sum(actual_counts.values()),
        }
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO backup_verifications VALUES (
                    ?, ?, ?, ?, ?, 'succeeded', ?, ?
                )
                """,
                [
                    f"backupverify_{uuid.uuid4().hex[:12]}",
                    backup_id,
                    result["archive_sha256"],
                    result["table_count"],
                    result["row_count"],
                    verified_by,
                    _utc_now_naive(),
                ],
            )
        finally:
            con.close()
        return result

    def restore(self, backup_id: str, target_path: Path) -> dict[str, Any]:
        if target_path.exists():
            raise ValueError("Restore target already exists")
        if target_path.resolve() == self.db_path.resolve():
            raise ValueError("Restore target must differ from the active database")
        verification = self.verify(
            backup_id,
            verified_by="restore",
        )
        archive_path = self._archive_path(backup_id)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = target_path.with_name(
            f".{target_path.name}.{uuid.uuid4().hex[:8]}.restore"
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix=".backup_restore_"
            ) as temporary:
                export_dir = Path(temporary) / "database"
                self._safe_extract(archive_path, export_dir)
                counts = self._import_and_count(export_dir, temporary_target)
            manifest = self._read_manifest(archive_path)
            if counts != manifest["table_counts"]:
                raise ValueError("Restored database failed the row-count check")
            os.replace(temporary_target, target_path)
        finally:
            temporary_target.unlink(missing_ok=True)
        return {
            **verification,
            "restored_path": str(target_path.resolve()),
        }

    def _export(
        self,
        export_dir: Path,
    ) -> tuple[dict[str, int], str]:
        con = connect(self.db_path)
        try:
            tables = [
                row[0]
                for row in con.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                ).fetchall()
            ]
            table_counts = {
                table: con.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
                for table in tables
            }
            duckdb_version = con.execute("SELECT version()").fetchone()[0]
            con.execute(
                f"EXPORT DATABASE {_sql_string(export_dir)} (FORMAT PARQUET)"
            )
        finally:
            con.close()
        return table_counts, duckdb_version

    def _import_and_count(
        self,
        export_dir: Path,
        target_path: Path,
    ) -> dict[str, int]:
        con = connect(target_path)
        try:
            con.execute(f"IMPORT DATABASE {_sql_string(export_dir)}")
            tables = [
                row[0]
                for row in con.execute(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                ).fetchall()
            ]
            return {
                table: con.execute(
                    f'SELECT COUNT(*) FROM "{table.replace(chr(34), chr(34) * 2)}"'
                ).fetchone()[0]
                for table in tables
            }
        finally:
            con.close()

    @staticmethod
    def _file_manifest(export_dir: Path) -> list[dict[str, Any]]:
        return [
            {
                "path": path.relative_to(export_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(export_dir.rglob("*"))
            if path.is_file()
        ]

    @staticmethod
    def _write_archive(export_dir: Path, archive_path: Path) -> None:
        with zipfile.ZipFile(
            archive_path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for path in sorted(export_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(export_dir).as_posix())

    @staticmethod
    def _read_manifest(archive_path: Path) -> dict[str, Any]:
        if not archive_path.exists():
            raise ValueError(f"Backup not found: {archive_path.stem}")
        with zipfile.ZipFile(archive_path) as archive:
            try:
                manifest = json.loads(
                    archive.read("manifest.json").decode("utf-8")
                )
            except KeyError as exc:
                raise ValueError("Backup manifest is missing") from exc
        if manifest.get("format_version") != BACKUP_FORMAT_VERSION:
            raise ValueError("Unsupported backup format")
        return manifest

    @staticmethod
    def _safe_extract(archive_path: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=False)
        target_root = target_dir.resolve()
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > 10_000:
                raise ValueError("Backup contains too many files")
            if sum(member.file_size for member in members) > 20_000_000_000:
                raise ValueError("Backup expands beyond the restore limit")
            for member in members:
                destination = (target_dir / member.filename).resolve()
                if target_root not in destination.parents:
                    raise ValueError("Backup contains an unsafe path")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(block)

    @staticmethod
    def _verify_export_files(
        export_dir: Path,
        manifest: dict[str, Any],
    ) -> None:
        expected = {
            item["path"]: item
            for item in manifest.get("files", [])
        }
        actual_paths = {
            path.relative_to(export_dir).as_posix()
            for path in export_dir.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        if actual_paths != set(expected):
            raise ValueError("Backup file inventory does not match the manifest")
        for relative_path, item in expected.items():
            path = export_dir / relative_path
            if path.stat().st_size != item["size_bytes"]:
                raise ValueError(f"Backup file size mismatch: {relative_path}")
            if _sha256(path) != item["sha256"]:
                raise ValueError(f"Backup checksum mismatch: {relative_path}")

    def _archive_path(self, backup_id: str) -> Path:
        if (
            not backup_id.startswith("backup_")
            or any(
                character
                not in (
                    "abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789_-"
                )
                for character in backup_id
            )
        ):
            raise ValueError("Invalid backup ID")
        return self.backup_dir / f"{backup_id}.zip"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
