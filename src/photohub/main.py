from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from .config import resolve_app_paths
from .db import create_session_factory, create_sqlite_engine, init_db
from .services import (
    CullingService,
    EditService,
    ExportService,
    ImportService,
    PresetService,
    ProjectService,
    StorageService,
)


@dataclass
class RuntimeBundle:
    paths: object
    engine: object
    session_factory: object
    project_service: ProjectService
    preset_service: PresetService
    culling_service: CullingService
    edit_service: EditService
    import_service: ImportService
    export_service: ExportService


def build_runtime() -> RuntimeBundle:
    paths = resolve_app_paths()
    engine = create_sqlite_engine(paths.db_path)
    init_db(engine)
    session_factory = create_session_factory(engine)
    return RuntimeBundle(
        paths=paths,
        engine=engine,
        session_factory=session_factory,
        project_service=ProjectService(session_factory=session_factory, paths=paths),
        preset_service=PresetService(session_factory=session_factory),
        culling_service=CullingService(session_factory=session_factory),
        edit_service=EditService(session_factory=session_factory),
        import_service=ImportService(session_factory=session_factory),
        export_service=ExportService(session_factory=session_factory),
    )


def main() -> int:
    # Build QApplication first to avoid any indirect QWidget construction
    # from optional UI dependencies during startup.
    app = QApplication(sys.argv)

    runtime = build_runtime()
    storage_service = StorageService()

    def reload_runtime():
        nonlocal runtime
        runtime.engine.dispose()
        runtime = build_runtime()
        return runtime

    # Delay UI module import until QApplication exists.
    from .ui import MainWindow

    window = MainWindow(
        project_service=runtime.project_service,
        preset_service=runtime.preset_service,
        culling_service=runtime.culling_service,
        edit_service=runtime.edit_service,
        import_service=runtime.import_service,
        export_service=runtime.export_service,
        storage_service=storage_service,
        on_reload_runtime=reload_runtime,
    )
    window.show()
    code = app.exec()
    runtime.engine.dispose()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
