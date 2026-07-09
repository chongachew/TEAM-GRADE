"""
Static regression guard for the settings-attribute bug class found during the
multi-player pivot: three separate `ingest/stages/*.py` files referenced
settings.SOMETHING that was never defined in config/settings.py, each silently
caught by a broad except block, quietly breaking rep_extraction/torso_crop/
biomechanics in production. This walks every stage file's AST for
`settings.<NAME>` attribute access and asserts the attribute actually exists.

Known, confirmed-dead files are excluded (see EXCLUDED_FILES) - they reference
settings.POSE_MODEL_NAME, which also doesn't exist, but neither file is wired
into STAGE_HANDLERS so it's not a live bug, just dead code.
"""

import ast
from pathlib import Path

from config import settings

STAGES_DIR = Path(__file__).parent.parent / "ingest" / "stages"

EXCLUDED_FILES = {
    "pose_stage.py",  # confirmed dead, not in STAGE_HANDLERS
    "EXAMPLE_REFACTORED_POSE_STAGE.py",  # confirmed dead, not in STAGE_HANDLERS
}


def _find_settings_attr_references(py_file: Path) -> set:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    attrs = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
        ):
            attrs.add(node.attr)
    return attrs


def test_no_missing_settings_attributes_in_live_stages():
    missing = {}
    for py_file in sorted(STAGES_DIR.glob("*.py")):
        if py_file.name in EXCLUDED_FILES or py_file.name == "__init__.py":
            continue
        attrs = _find_settings_attr_references(py_file)
        file_missing = {attr for attr in attrs if not hasattr(settings, attr)}
        if file_missing:
            missing[py_file.name] = file_missing

    assert not missing, (
        "Found settings.<NAME> references with no matching definition in "
        f"config/settings.py (these raise AttributeError at runtime, often "
        f"silently caught by a broad except): {missing}"
    )
