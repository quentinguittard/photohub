from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..models import Client, Preset, PresetVersion, Project
from ..preset_defaults import default_preset_config
from .watermarks import normalize_watermark_config


class PresetService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def list_presets(self) -> list[Preset]:
        with self.session_factory() as session:
            query = (
                select(Preset)
                .options(selectinload(Preset.versions), selectinload(Preset.projects))
                .order_by(Preset.name.asc())
            )
            return list(session.scalars(query).all())

    def get_preset(self, preset_id: int) -> Preset | None:
        with self.session_factory() as session:
            query = (
                select(Preset)
                .options(selectinload(Preset.versions), selectinload(Preset.projects))
                .where(Preset.id == preset_id)
            )
            return session.scalar(query)

    def create_preset(
        self,
        name: str,
        scope: str = "global",
        scope_ref_id: int | None = None,
        config: Mapping | None = None,
    ) -> Preset:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Le nom du preset est requis.")

        payload = dict(config) if config is not None else default_preset_config()

        with self.session_factory() as session:
            if session.scalar(select(Preset).where(Preset.name == clean_name)) is not None:
                raise ValueError("Un preset avec ce nom existe deja.")

            preset = Preset(
                name=clean_name,
                scope=scope,
                scope_ref_id=scope_ref_id,
                is_active=True,
                config_json=json.dumps(payload, ensure_ascii=True, indent=2),
            )
            session.add(preset)
            session.flush()
            self._append_version(session, preset, preset.config_json)
            session.commit()
            session.refresh(preset)
            return preset

    def update_preset(
        self,
        preset_id: int,
        name: str,
        scope: str = "global",
        scope_ref_id: int | None = None,
        config: Mapping | None = None,
    ) -> Preset:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Le nom du preset est requis.")
        payload = dict(config) if config is not None else default_preset_config()

        with self.session_factory() as session:
            preset = session.get(Preset, preset_id)
            if preset is None:
                raise ValueError("Preset introuvable.")

            duplicate = session.scalar(select(Preset).where(Preset.name == clean_name, Preset.id != preset_id))
            if duplicate is not None:
                raise ValueError("Un autre preset avec ce nom existe deja.")

            preset.name = clean_name
            preset.scope = scope
            preset.scope_ref_id = scope_ref_id
            preset.config_json = json.dumps(payload, ensure_ascii=True, indent=2)
            self._append_version(session, preset, preset.config_json)

            session.commit()
            session.refresh(preset)
            return preset

    def delete_preset(self, preset_id: int) -> None:
        with self.session_factory() as session:
            preset = session.get(Preset, preset_id)
            if preset is None:
                raise ValueError("Preset introuvable.")
            session.delete(preset)
            session.commit()

    def list_versions(self, preset_id: int) -> list[PresetVersion]:
        with self.session_factory() as session:
            query = (
                select(PresetVersion)
                .where(PresetVersion.preset_id == preset_id)
                .order_by(PresetVersion.version.desc(), PresetVersion.created_at.desc())
            )
            versions = list(session.scalars(query).all())
            if versions:
                return versions

            preset = session.get(Preset, preset_id)
            if preset is None:
                return []

            self._append_version(session, preset, preset.config_json)
            session.commit()
            return list(session.scalars(query).all())

    def rollback_to_version(self, preset_id: int, version_id: int) -> Preset:
        with self.session_factory() as session:
            preset = session.get(Preset, preset_id)
            if preset is None:
                raise ValueError("Preset introuvable.")

            target = session.get(PresetVersion, version_id)
            if target is None or target.preset_id != preset_id:
                raise ValueError("Version introuvable pour ce preset.")

            preset.config_json = target.config_json
            self._append_version(session, preset, preset.config_json)
            session.commit()
            session.refresh(preset)
            return preset

    def parse_config(self, config_text: str) -> dict:
        try:
            parsed = json.loads(config_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON preset invalide: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Le JSON preset doit etre un objet.")
        return parsed

    def resolve_effective_config_for_project(self, project_id: int) -> dict:
        with self.session_factory() as session:
            query = select(Project).options(selectinload(Project.preset)).where(Project.id == project_id)
            project = session.scalar(query)
            if project is None:
                raise ValueError("Projet introuvable.")
            return resolve_effective_config_for_project_model(session, project)

    def list_client_refs(self) -> list[tuple[int, str]]:
        with self.session_factory() as session:
            query = select(Client).order_by(Client.name.asc())
            clients = list(session.scalars(query).all())
            return [(item.id, item.name) for item in clients]

    def list_project_refs(self) -> list[tuple[int, str]]:
        with self.session_factory() as session:
            query = select(Project).order_by(Project.created_at.desc())
            projects = list(session.scalars(query).all())
            return [(item.id, item.name) for item in projects]

    @staticmethod
    def _append_version(session, preset: Preset, config_json: str) -> PresetVersion:
        max_version = session.scalar(
            select(PresetVersion.version)
            .where(PresetVersion.preset_id == preset.id)
            .order_by(PresetVersion.version.desc())
            .limit(1)
        )
        next_version = 1 if max_version is None else int(max_version) + 1
        version = PresetVersion(
            preset_id=preset.id,
            version=next_version,
            config_json=config_json,
        )
        session.add(version)
        return version


def resolve_effective_config_for_project_model(session, project: Project) -> dict:
    effective = default_preset_config()

    if project.preset is not None and project.preset.is_active:
        effective = _merge_with_preset_json(effective, project.preset.config_json)

    effective["watermark"] = normalize_watermark_config(effective.get("watermark", {}))

    return effective


def _merge_with_preset_json(base: dict, config_json: str) -> dict:
    try:
        payload = json.loads(config_json)
    except Exception:
        return base
    if not isinstance(payload, dict):
        return base
    return _deep_merge_dict(base, payload)


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged
