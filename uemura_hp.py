"""Streamlit Cloud 用エントリーポイント（uemura.py と同一アプリ）"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_app_path = Path(__file__).resolve().parent / "uemura.py"
_spec = spec_from_file_location("miratech_uemura", _app_path)
_module = module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_module)
