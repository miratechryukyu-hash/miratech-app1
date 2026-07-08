import base64
from io import BytesIO
from pathlib import Path
from typing import Optional

import streamlit.components.v1 as components

frontend_dir = (Path(__file__).resolve().parent / "back_camera_input_frontend").absolute()
_component_func = components.declare_component(
    "back_camera_input", path=str(frontend_dir)
)


def back_camera_input(
    height: int = 450,
    width: int = 500,
    key: Optional[str] = None,
) -> Optional[BytesIO]:
    """アウトカメラ（背面カメラ）を優先して撮影するコンポーネント"""
    b64_data: Optional[str] = _component_func(
        height=height,
        width=width,
        key=key,
    )

    if b64_data is None:
        return None

    raw_data = b64_data.split(",")[1]
    return BytesIO(base64.b64decode(raw_data))
