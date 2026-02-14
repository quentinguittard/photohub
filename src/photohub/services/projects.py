from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..config import AppPaths
from ..models import Client, Preset, Project
from .quality_checks import (
    assert_quality_for_export,
    default_project_quality_config,
    evaluate_quality,
    load_project_quality_payload,
    normalize_quality_config,
    normalize_quality_validation,
    validate_quality_manually,
)
from ..utils import slugify


PROJECT_STATUSES: tuple[str, ...] = (
    "a_importer",
    "importe",
    "en_tri",
    "pret_a_livrer",
    "archive",
)

PROJECT_STATUS_LABELS: dict[str, str] = {
    "a_importer": "A importer",
    "importe": "Importe",
    "en_tri": "En tri",
    "pret_a_livrer": "Pret a livrer",
    "archive": "Archive",
}

PROJECT_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "a_importer": ("importe",),
    "importe": ("en_tri", "pret_a_livrer"),
    "en_tri": ("pret_a_livrer",),
    "pret_a_livrer": ("en_tri", "archive"),
    "archive": (),
}


def status_label(status: str) -> str:
    return PROJECT_STATUS_LABELS.get(status, status)


def can_transition_status(current_status: str, target_status: str) -> bool:
    if current_status == target_status:
        return True
    return target_status in PROJECT_STATUS_TRANSITIONS.get(current_status, ())


def try_transition_project_status(project: Project, target_status: str) -> bool:
    clean_target = (target_status or "").strip()
    if clean_target not in PROJECT_STATUSES:
        return False
    current = (project.status or "").strip()
    if current not in PROJECT_STATUSES:
        current = "a_importer"
    if can_transition_status(current, clean_target):
        project.status = clean_target
        return True
    return False


def _next_status_labels(current_status: str) -> list[str]:
    targets = PROJECT_STATUS_TRANSITIONS.get(current_status, ())
    return [status_label(code) for code in targets]


def build_invalid_transition_message(current_status: str, target_status: str) -> str:
    current_label = status_label(current_status)
    target_label = status_label(target_status)
    allowed = _next_status_labels(current_status)
    if not allowed:
        return (
            f"Transition invalide: '{current_label}' -> '{target_label}'. "
            "Ce statut est final."
        )
    return (
        f"Transition invalide: '{current_label}' -> '{target_label}'. "
        f"Transitions autorisees: {', '.join(allowed)}."
    )


class ProjectService:
    def __init__(self, session_factory, paths: AppPaths):
        self.session_factory = session_factory
        self.paths = paths

    def list_projects(self) -> list[Project]:
        with self.session_factory() as session:
            query = (
                select(Project)
                .options(selectinload(Project.preset), selectinload(Project.client))
                .order_by(Project.created_at.desc())
            )
            return list(session.scalars(query).all())

    def get_project(self, project_id: int) -> Project | None:
        with self.session_factory() as session:
            query = (
                select(Project)
                .options(selectinload(Project.preset), selectinload(Project.client))
                .where(Project.id == project_id)
            )
            return session.scalar(query)

    def create_project(
        self,
        name: str,
        shoot_date: date,
        preset_id: int | None = None,
        custom_root_path: str | None = None,
        client_name: str | None = None,
    ) -> Project:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Le nom du projet est requis.")

        folder_name = f"{shoot_date.strftime('%Y%m%d')}_{slugify(clean_name)}"
        if custom_root_path:
            base_dir = Path(custom_root_path).expanduser().resolve()
            if not base_dir.exists():
                raise ValueError("Le dossier personnalise n'existe pas.")
            if not base_dir.is_dir():
                raise ValueError("Le chemin personnalise n'est pas un dossier.")
            project_root = self._allocate_project_dir(folder_name, base_dir=base_dir)
        else:
            project_root = self._allocate_project_dir(folder_name)

        (project_root / "raw").mkdir(parents=True, exist_ok=True)
        (project_root / "exports").mkdir(parents=True, exist_ok=True)
        (project_root / "backup").mkdir(parents=True, exist_ok=True)

        with self.session_factory() as session:
            if preset_id is not None:
                preset = session.get(Preset, preset_id)
                if preset is None:
                    raise ValueError("Preset introuvable pour ce projet.")

            client_id = None
            clean_client_name = (client_name or "").strip()
            if clean_client_name:
                client = session.scalar(
                    select(Client).where(func.lower(Client.name) == clean_client_name.lower())
                )
                if client is None:
                    client = Client(name=clean_client_name)
                    session.add(client)
                    session.flush()
                client_id = client.id

            project = Project(
                name=clean_name,
                shoot_date=shoot_date,
                status="a_importer",
                root_path=str(project_root),
                quality_check_config_json=json.dumps(default_project_quality_config(), ensure_ascii=True),
                quality_check_validation_json=json.dumps({}, ensure_ascii=True),
                client_id=client_id,
                preset_id=preset_id,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            return project

    def assign_preset(self, project_id: int, preset_id: int | None) -> None:
        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError("Projet introuvable.")

            if preset_id is not None and session.get(Preset, preset_id) is None:
                raise ValueError("Preset introuvable.")

            project.preset_id = preset_id
            session.commit()

    def list_allowed_statuses(self) -> list[str]:
        return list(PROJECT_STATUSES)

    def list_status_choices(self) -> list[tuple[str, str]]:
        return [(code, status_label(code)) for code in PROJECT_STATUSES]

    def get_status_label(self, status: str) -> str:
        return status_label(status)

    def try_update_project_status(self, project: Project, status: str) -> bool:
        return try_transition_project_status(project, status)

    def update_project_status(self, project_id: int, status: str) -> None:
        clean_status = (status or "").strip()
        if clean_status not in PROJECT_STATUSES:
            raise ValueError("Statut projet invalide.")

        with self.session_factory() as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError("Projet introuvable.")
            current = (project.status or "").strip() or "a_importer"
            if not can_transition_status(current, clean_status):
                raise ValueError(build_invalid_transition_message(current, clean_status))
            project.status = clean_status
            session.commit()

    def get_quality_check(self, project_id: int, export_min_rating: int | None = None) -> dict:
        with self.session_factory() as session:
            project = session.get(Project, int(project_id))
            if project is None:
                raise ValueError("Projet introuvable.")
            config, validation = load_project_quality_payload(project)
            evaluation = evaluate_quality(
                session=session,
                project=project,
                export_min_rating=export_min_rating,
                config=config,
                validation=validation,
            )
            payload = evaluation.to_dict()
            payload.update(
                {
                    "project_id": int(project.id),
                    "config": dict(config),
                    "validation": dict(validation),
                }
            )
            return payload

    def update_quality_check(self, project_id: int, config: dict) -> dict:
        with self.session_factory() as session:
            project = session.get(Project, int(project_id))
            if project is None:
                raise ValueError("Projet introuvable.")
            normalized_config = normalize_quality_config(config)
            project.quality_check_config_json = json.dumps(normalized_config, ensure_ascii=True)
            project.quality_check_validation_json = json.dumps({}, ensure_ascii=True)
            session.commit()

        return self.get_quality_check(project_id=int(project_id), export_min_rating=1)

    def validate_quality_check(self, project_id: int) -> dict:
        with self.session_factory() as session:
            project = session.get(Project, int(project_id))
            if project is None:
                raise ValueError("Projet introuvable.")
            config, _ = load_project_quality_payload(project)
            validation = validate_quality_manually(session=session, project=project, config=config)
            project.quality_check_validation_json = json.dumps(
                normalize_quality_validation(validation),
                ensure_ascii=True,
            )
            session.commit()

        return self.get_quality_check(project_id=int(project_id), export_min_rating=1)

    def assert_export_quality(self, project_id: int, export_min_rating: int) -> dict:
        with self.session_factory() as session:
            project = session.get(Project, int(project_id))
            if project is None:
                raise ValueError("Projet introuvable.")
            config, validation = load_project_quality_payload(project)
            evaluation = assert_quality_for_export(
                session=session,
                project=project,
                export_min_rating=int(export_min_rating),
                config=config,
                validation=validation,
            )
            payload = evaluation.to_dict()
            payload.update(
                {
                    "project_id": int(project.id),
                    "config": dict(config),
                    "validation": dict(validation),
                }
            )
            return payload

    def _allocate_project_dir(self, base_name: str, base_dir: Path | None = None) -> Path:
        target_root = base_dir if base_dir is not None else self.paths.projects_dir
        candidate = target_root / base_name
        if not candidate.exists():
            return candidate

        index = 1
        while True:
            alt = target_root / f"{base_name}_{index}"
            if not alt.exists():
                return alt
            index += 1
