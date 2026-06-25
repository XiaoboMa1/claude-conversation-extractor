## Packaging: when to update `pyproject.toml`

This project uses `pyproject.toml` as the **sole packaging config**. `setup.py` exists but pip ignores its `entry_points`, `version`, `py_modules`, and `install_requires` fields whenever `pyproject.toml` declares a `[project]` section.

After `pip install -e .`, pip generates `.exe` scripts (Windows) or shell wrappers (Linux) based on `[project.scripts]`. These wrappers hardcode the import path at install time. If the code moves but `pyproject.toml` isn't updated, the installed commands break with `ModuleNotFoundError`.

### You must edit `pyproject.toml` when:

1. **A CLI entry-point function moves** (module rename, function rename, package restructure)
   - Field: `[project.scripts]`
   - Example: moved `extract_claude_logs:launch_interactive` → `claude_extractor.cli.main:launch_interactive`

2. **Version bump**
   - Field: `version` under `[project]`

3. **New runtime dependency added**
   - Field: `dependencies` under `[project]`

4. **Package directory layout changes** (new subpackage, rename `src/` → `lib/`, etc.)
   - Field: `[tool.setuptools.packages.find]` — controls where `find_packages()` looks
   - Field: `[tool.setuptools.package-dir]` — maps package root to directory

### After editing `pyproject.toml`:

```bash
pip install -e .
```

This regenerates the `.exe` wrappers and `.egg-info`. Without reinstall, changes have no effect on the installed commands.

### You do NOT need to touch `pyproject.toml` for:

- Adding/changing code within existing modules (editable install auto-picks up changes)
- Adding new `.py` files inside an existing package (auto-discovered by `find_packages`)
- Changing test files