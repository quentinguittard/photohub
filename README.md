# PhotoHub

PhotoHub is a cross-platform desktop toolbox for photographers (Windows/macOS), focused on:

- project hub
- secure import with checksum
- quick culling (rating/reject + filters)
- edit mode with quick controls (exposure, WB, crop, straighten) and advanced controls
- copy/paste/sync of edit settings across filtered assets
- presets de projet (CRUD + versioning + rollback)
- presets editor in both form mode (non-tech) and JSON mode
- contextual tooltips in preset form for non-technical users
- multi-profile export (`web`, `print`, `social`)
- delivery options (ZIP package + export report)
- contact sheet PDF generation
- background jobs with progress/cancel for import/export/culling batch
- export queue with ETA, pause/resume, retry failed jobs
- simple preset model: create a preset, then assign it to a project
- Sprint UI-1 shell: Fluent-inspired navigation, top bar context, dashboard, jobs center

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
photohub
```

Optional Fluent UI (PySide6 build):

```bash
pip install -e ".[fluent]"
```

If Fluent is enabled but the installed `qfluentwidgets` package is a `PyQt5` build,
PhotoHub now auto-disables Fluent mode to avoid startup crashes.

## Notes

- Local data and SQLite DB are stored in an OS-specific application directory:
  - Windows: `%APPDATA%\PhotoHub`
  - macOS: `~/Library/Application Support/PhotoHub`
- Project assets are copied into project folders under `projects/` in that app data directory.
- You can change the global storage location in the `Settings` tab.
- You can also set a custom parent folder per project in `Hub Projets`.
- Culling shortcuts in `Tri`: keys `0..5` rating, `P` keep, `X` reject, `R` toggle reject, `Left/Right` previous/next, `Space` next, `F` focus mode.
- Edit shortcuts in `Edit`: `Ctrl+C` copy settings, `Ctrl+V` paste settings, `Ctrl+S` apply, `Shift+S` sync to filtered assets, `Y` before/after.
- In `Export`, you can generate a delivery ZIP and a `.txt` report.
- In `Export`, you can also generate a contact sheet PDF.
- In `Hub Projets`, you can change the status of the selected project manually.
- Project status transitions are controlled to avoid inconsistent workflow jumps.
- In `Presets`, each preset now shows which project(s) currently use it.
