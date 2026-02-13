# PhotoHub

PhotoHub is a cross-platform desktop toolbox for photographers (Windows/macOS), focused on:

- project hub
- secure import with checksum
- quick culling (rating/reject + filters)
- presets de projet (CRUD + versioning + rollback)
- presets editor in both form mode (non-tech) and JSON mode
- contextual tooltips in preset form for non-technical users
- multi-profile export (`web`, `print`, `social`)
- delivery options (ZIP package + export report)
- contact sheet PDF generation
- background jobs with progress/cancel for import/export/culling batch
- simple preset model: create a preset, then assign it to a project
- Sprint UI-1 shell: Fluent-inspired navigation, top bar context, dashboard, jobs center

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
photohub
```

## Notes

- Local data and SQLite DB are stored in an OS-specific application directory:
  - Windows: `%APPDATA%\PhotoHub`
  - macOS: `~/Library/Application Support/PhotoHub`
- Project assets are copied into project folders under `projects/` in that app data directory.
- You can change the global storage location in the `Settings` tab.
- You can also set a custom parent folder per project in `Hub Projets`.
- Culling shortcuts in `Tri`: keys `0..5` to set rating, `R` to toggle reject.
- In `Export`, you can generate a delivery ZIP and a `.txt` report.
- In `Export`, you can also generate a contact sheet PDF.
- In `Hub Projets`, you can change the status of the selected project manually.
- Project status transitions are controlled to avoid inconsistent workflow jumps.
- In `Presets`, each preset now shows which project(s) currently use it.
