import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
import pandas as pd
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from itsdangerous import URLSafeTimedSerializer
import qrcode
from io import BytesIO
import json
import re
import os
import requests
from PIL import Image
import base64
import time
import html
import hashlib
from pathlib import Path
import calendar

def _is_streamlit_cloud():
    """Streamlit Community Cloud 上で動いているか判定"""
    if Path("/mount/src").exists():
        return True
    host = os.environ.get("HOSTNAME", "")
    return host.endswith(".streamlit.app")

def _upload_fallback_camera(height=450, width=500, key=None):
    st.caption("スマホの場合「ファイルを選択」→「写真を撮る」でアウトカメラが使えます")
    uploaded = st.file_uploader(
        "銘板写真を撮影または選択",
        type=["jpg", "jpeg", "png", "webp"],
        key=key or "camera_upload",
    )
    if uploaded is None:
        return None
    return BytesIO(uploaded.getvalue())

def _init_back_camera_input():
    """アウトカメラ撮影。Cloudでは file_uploader、ローカルでは custom component"""
    if _is_streamlit_cloud():
        return _upload_fallback_camera

    try:
        bundled = Path(__file__).resolve().parent / "back_camera_input_frontend"
        if bundled.is_dir() and (bundled / "index.html").is_file():
            component_func = components.declare_component(
                "miratech_back_camera", path=str(bundled)
            )

            def capture(height=450, width=500, key=None):
                b64_data = component_func(height=height, width=width, key=key)
                if b64_data is None:
                    return None
                return BytesIO(base64.b64decode(b64_data.split(",")[1]))

            return capture
    except Exception:
        pass

    try:
        from streamlit_back_camera_input import back_camera_input as pip_capture
        return pip_capture
    except Exception:
        pass

    return _upload_fallback_camera

try:
    back_camera_input = _init_back_camera_input()
except Exception:
    def back_camera_input(height=450, width=500, key=None):
        st.warning("カメラ機能は現在利用できません。手動入力で登録してください。")
        return None

# ==========================================
# 設定
# ==========================================
APP_URL = "https://miratech-app1-dzi7pmrrt5nzqt6be6swzn.streamlit.app/"
APP_VERSION = "2026-08-07f"

# 全点検表共通の判定記号
INSPECTION_CHECK_OPTIONS = ["〇", "△", "×", "---"]
INSPECTION_CHECK_LEGEND = "判定: 〇=良好 / △=修理検討 / ×=不合格 / ---=機能なし"
INCU_I_CHECK_OPTIONS = INSPECTION_CHECK_OPTIONS

def normalize_check_symbol(val):
    v = clean_data_str(val)
    if v == "OK":
        return "〇"
    if v == "NG":
        return "×"
    return v

def is_check_incomplete(val):
    return val in (None, "", "--")

def is_check_ng(val):
    return normalize_check_symbol(val) in ("×", "△")

def measure_judge(in_range):
    return "〇" if in_range else "×"

def leakage_ua_int(val):
    try:
        return int(round(float(val or 0)))
    except (TypeError, ValueError):
        return 0

def format_leakage_ua(val):
    return f"{leakage_ua_int(val)}μA"

def _leakage_number_input(label, current, key):
    raw = st.number_input(
        label, min_value=0, value=leakage_ua_int(current), step=1, format="%d", key=key,
    )
    return leakage_ua_int(raw)

def check_cell_style_class(cell):
    sym = normalize_check_symbol(cell)
    if sym == "×":
        return "ng"
    if sym == "〇":
        return "ok"
    if sym == "△":
        return "warn"
    if sym == "---":
        return "na"
    return ""

# 輸液ポンプ専用点検項目（入力フォーム・印刷レイアウト共通）
INFUSION_PUMP_APPEARANCE_ITEMS = [
    "本体の汚れ・破損なし",
    "ポールクランプ用ネジ穴",
    "チューブクランプ動作",
    "フィンガー部動作",
    "AC・DC切り替え",
    "セルフチェック機能",
    "表示部LED",
]
INFUSION_PUMP_ALARM_ITEMS = [
    "開始忘れ警報",
    "気泡検出",
    "輸液完了",
    "再警報",
    "流量設定無し警報",
    "ドアオープン警報",
    "消音",
]
INFUSION_PUMP_FUNCTION_ITEMS = [
    "積算クリア機能",
    "日付・時刻設定",
    "流量設定",
]

def default_infusion_pump_checks():
    return {label: "---" for label in INFUSION_PUMP_ALARM_ITEMS + INFUSION_PUMP_FUNCTION_ITEMS}

# アトム保育器 インキュi 定期点検表（R3.1.28）
INCU_I_APPEARANCE_ITEMS = [
    "本体外装部に劣化・破損はないか",
    "電源コードに劣化・破損はないか",
    "ディスプレイ用ケーブル及び表示部に破損はないか",
    "センサーモジュールに変形・破損はないか",
    "フード・処置窓・処置窓開閉ノブ・手入れ用処置窓ロックレバー・インナーパネルに破損はないか",
    "パッキンに劣化・破損はないか",
    "マットレス・X線カセットトレー・臥床台・中床・ファンカバー・ヒーター・ファンに破損はないか",
    "フィルターの交換時期は適切か",
]
INCU_I_FUNCTION_ITEMS = [
    "電源のON/OFFが可能か",
    "ファンモーターから異音がないか",
    "傾斜装置はスムーズに動作するか",
]
INCU_I_OPERATION_CHECK_ITEMS = [
    "加湿槽・カートリッジタンクに破損・変形はないか",
    "酸素センサーにて21%校正ができるか",
    "SpO2表示と脈拍数が表示されるか",
]
INCU_I_ALARM_ITEMS = [
    "ファンカバー外れ警報",
    "ファン外れ警報",
    "水なし警報",
    "水槽外れ警報",
    "酸素配管接続不良警報",
    "体温プローブ警報",
    "設定温度警報(マニュアルコントロール)",
    "設定温度警報(サーボコントロール)",
    "過温警報",
    "システム警報",
    "停電警報(停電警報灯の点灯)",
]
INCU_I_LEGACY_CHECKS = {
    "チェックスイッチ": "---", "設定温度警報(マニュアル)": "---", "設定温度警報(皮膚温)": "---",
    "プローブ警報": "---", "停電警報": "---", "キャノピ傾斜": "---",
    "蘇生装置": "---", "酸素ブレンダ作動": "---", "供給ガス警報": "---",
    "吸引・流量計": "---", "外装・キャノピ・ネジ類": "---", "電源・ジャック・ガード": "---",
}

def default_incu_i_checks():
    checks = {}
    for label in (
        INCU_I_APPEARANCE_ITEMS + INCU_I_FUNCTION_ITEMS
        + INCU_I_OPERATION_CHECK_ITEMS + INCU_I_ALARM_ITEMS
    ):
        checks[label] = "---"
    return checks

def default_incu_i_measurements():
    return {
        "体重モニタ(中央)": 5000.0,
        "体重モニタ(四隅)": 5000.0,
        "湿度コントロール(表示)": 90.0,
        "湿度コントロール(実測)": 90.0,
        "マニュアルコントロール(表示)": 34.0,
        "マニュアルコントロール(実測)": 34.0,
        "サーボコントロール(表示)": 34.0,
        "サーボコントロール(実測)": 34.0,
        "酸素濃度制御(表示)": 40.0,
        "酸素濃度制御(実測)": 40.0,
        "酸素接続口1(実測)": 65.0,
        "接地漏れ電流(正常)": 0,
        "接地漏れ電流(単一故障)": 0,
        "外装漏れ電流(正常)": 0,
        "外装漏れ電流(単一故障)": 0,
        "患者漏れ電流I(正常)": 0,
        "患者漏れ電流I(単一故障)": 0,
    }

def is_incu_i_incubator(device_category, device_model):
    if clean_data_str(device_category) != "保育器":
        return False
    model = clean_data_str(device_model).lower().replace(" ", "")
    return any(token in model for token in ("incui", "incu-i", "incu_i", "インキュi", "インキュｉ"))

def _incu_i_symbol_judge(val):
    sym = clean_data_str(val)
    if sym == "×":
        return "×"
    if sym == "△":
        return "△"
    if sym == "〇":
        return "〇"
    return sym or "-"

def _incu_i_range_judge(val, lo, hi):
    return "〇" if lo <= val <= hi else "×"

def render_incu_i_inspection_fields(incu_i_checks, incu_i_measurements):
    """インキュi 定期点検表の入力欄（フォーム内で呼び出す）"""
    opts = INSPECTION_CHECK_OPTIONS
    st.caption(INSPECTION_CHECK_LEGEND)

    st.write("**1. 外観点検**")
    c1, c2 = st.columns(2)
    for idx, label in enumerate(INCU_I_APPEARANCE_ITEMS):
        with (c1 if idx % 2 == 0 else c2):
            incu_i_checks[label] = st.radio(
                label, opts, horizontal=True, index=None, key=f"incu_i_app_{idx}",
            )

    st.write("**2. 機能点検**")
    c1, c2 = st.columns(2)
    for idx, label in enumerate(INCU_I_FUNCTION_ITEMS):
        with (c1 if idx % 2 == 0 else c2):
            incu_i_checks[label] = st.radio(
                label, opts, horizontal=True, index=None, key=f"incu_i_func_{idx}",
            )

    st.write("**体重モニタテスト**")
    st.caption("体重計の校正(5kg) → サービス画面より")
    w1, w2 = st.columns(2)
    with w1:
        st.caption("中央 表示値: 5000±5g")
        incu_i_measurements["体重モニタ(中央)"] = st.number_input(
            "中央 表示値 (g)", value=float(incu_i_measurements["体重モニタ(中央)"]), step=1.0,
            key="incu_i_w_center",
        )
    with w2:
        st.caption("四隅 表示値: 5000±10g")
        incu_i_measurements["体重モニタ(四隅)"] = st.number_input(
            "四隅 表示値 (g)", value=float(incu_i_measurements["体重モニタ(四隅)"]), step=1.0,
            key="incu_i_w_corner",
        )

    st.write("**3. 動作点検（保育器モード）**")
    incu_i_checks[INCU_I_OPERATION_CHECK_ITEMS[0]] = st.radio(
        INCU_I_OPERATION_CHECK_ITEMS[0], opts, horizontal=True, index=None, key="incu_i_op_tank",
    )

    st.write("**湿度コントロールテスト**")
    st.caption("設定90% / 表示90±3% / 実測90±10%　:red[**※必ず精製水使用する**]")
    
    h1, h2 = st.columns(2)
    with h1:
        incu_i_measurements["湿度コントロール(表示)"] = st.number_input(
            "湿度 表示値 (%)", value=float(incu_i_measurements["湿度コントロール(表示)"]), step=0.1,
            key="incu_i_hum_disp",
        )
    with h2:
        incu_i_measurements["湿度コントロール(実測)"] = st.number_input(
            "湿度 実測値 (%)", value=float(incu_i_measurements["湿度コントロール(実測)"]), step=0.1,
            key="incu_i_hum_meas",
        )

    st.write("**マニュアルコントロールテスト**")
    st.caption("設定34℃ / 表示・実測 34±1℃")
    m1, m2 = st.columns(2)
    with m1:
        incu_i_measurements["マニュアルコントロール(表示)"] = st.number_input(
            "マニュアル 表示値 (℃)", value=float(incu_i_measurements["マニュアルコントロール(表示)"]), step=0.1,
            key="incu_i_man_disp",
        )
    with m2:
        incu_i_measurements["マニュアルコントロール(実測)"] = st.number_input(
            "マニュアル 実測値 (℃)", value=float(incu_i_measurements["マニュアルコントロール(実測)"]), step=0.1,
            key="incu_i_man_meas",
        )

    st.write("**サーボコントロールテスト**")
    st.caption("設定34℃ / 表示・実測 34±0.5℃ :red[**※温度センサーを本体に接続する**]")
    s1, s2 = st.columns(2)
    with s1:
        incu_i_measurements["サーボコントロール(表示)"] = st.number_input(
            "サーボ 表示値 (℃)", value=float(incu_i_measurements["サーボコントロール(表示)"]), step=0.1,
            key="incu_i_srv_disp",
        )
    with s2:
        incu_i_measurements["サーボコントロール(実測)"] = st.number_input(
            "サーボ 実測値 (℃)", value=float(incu_i_measurements["サーボコントロール(実測)"]), step=0.1,
            key="incu_i_srv_meas",
        )

    incu_i_checks[INCU_I_OPERATION_CHECK_ITEMS[1]] = st.radio(
        INCU_I_OPERATION_CHECK_ITEMS[1], opts, horizontal=True, index=None, key="incu_i_o2_cal",
    )

    st.write("**酸素濃度制御テスト**")
    st.caption("設定40% / 表示・実測 40±3%")
    o1, o2 = st.columns(2)
    with o1:
        incu_i_measurements["酸素濃度制御(表示)"] = st.number_input(
            "酸素濃度 表示値 (%)", value=float(incu_i_measurements["酸素濃度制御(表示)"]), step=0.1,
            key="incu_i_o2_disp",
        )
    with o2:
        incu_i_measurements["酸素濃度制御(実測)"] = st.number_input(
            "酸素濃度 実測値 (%)", value=float(incu_i_measurements["酸素濃度制御(実測)"]), step=0.1,
            key="incu_i_o2_meas",
        )

    st.write("**酸素接続口1テスト**")
    st.caption("設定流量10L/min / 実測65%以上")
    incu_i_measurements["酸素接続口1(実測)"] = st.number_input(
        "酸素接続口1 実測値 (%)", value=float(incu_i_measurements["酸素接続口1(実測)"]), step=0.1,
        key="incu_i_o2_port",
    )

    incu_i_checks[INCU_I_OPERATION_CHECK_ITEMS[2]] = st.radio(
        INCU_I_OPERATION_CHECK_ITEMS[2], opts, horizontal=True, index=None, key="incu_i_spo2",
    )

    st.write("**4. 警報**")
    c1, c2 = st.columns(2)
    for idx, label in enumerate(INCU_I_ALARM_ITEMS):
        with (c1 if idx % 2 == 0 else c2):
            incu_i_checks[label] = st.radio(
                label, opts, horizontal=True, index=None, key=f"incu_i_alarm_{idx}",
            )

    st.write("**5. 電気的安全性**")
    st.caption("接地漏れ電流: 正常200μA以下 / 単一故障500μA以下")
    e1, e2 = st.columns(2)
    with e1:
        incu_i_measurements["接地漏れ電流(正常)"] = _leakage_number_input(
            "接地漏れ電流 正常 (μA)", incu_i_measurements["接地漏れ電流(正常)"], "incu_i_earth_n",
        )
    with e2:
        incu_i_measurements["接地漏れ電流(単一故障)"] = _leakage_number_input(
            "接地漏れ電流 単一故障 (μA)", incu_i_measurements["接地漏れ電流(単一故障)"], "incu_i_earth_f",
        )
    st.caption("外装漏れ電流: 正常100μA以下 / 単一故障500μA以下")
    e3, e4 = st.columns(2)
    with e3:
        incu_i_measurements["外装漏れ電流(正常)"] = _leakage_number_input(
            "外装漏れ電流 正常 (μA)", incu_i_measurements["外装漏れ電流(正常)"], "incu_i_enc_n",
        )
    with e4:
        incu_i_measurements["外装漏れ電流(単一故障)"] = _leakage_number_input(
            "外装漏れ電流 単一故障 (μA)", incu_i_measurements["外装漏れ電流(単一故障)"], "incu_i_enc_f",
        )
    st.caption("患者漏れ電流I: 正常100μA以下 / 単一故障500μA以下")
    e5, e6 = st.columns(2)
    with e5:
        incu_i_measurements["患者漏れ電流I(正常)"] = _leakage_number_input(
            "患者漏れ電流I 正常 (μA)", incu_i_measurements["患者漏れ電流I(正常)"], "incu_i_pat_n",
        )
    with e6:
        incu_i_measurements["患者漏れ電流I(単一故障)"] = _leakage_number_input(
            "患者漏れ電流I 単一故障 (μA)", incu_i_measurements["患者漏れ電流I(単一故障)"], "incu_i_pat_f",
        )

def build_incu_i_report_sections(incu_i_checks, incu_i_measurements):
    m = incu_i_measurements
    c = incu_i_checks
    return {
        "form": "incu_i",
        "title": "アトム保育器 インキュi 定期点検表",
        "sections": [
            {
                "title": "1. 外観点検",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in INCU_I_APPEARANCE_ITEMS],
            },
            {
                "title": "2. 機能点検",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in INCU_I_FUNCTION_ITEMS],
            },
            {
                "title": "体重モニタテスト",
                "kind": "measure",
                "items": [
                    _measured_item("中央 表示値", "5000±5g", "5000±5g", f"{m['体重モニタ(中央)']}g",
                                   _incu_i_range_judge(m["体重モニタ(中央)"], 4995, 5005)),
                    _measured_item("四隅 表示値", "5000±10g", "5000±10g", f"{m['体重モニタ(四隅)']}g",
                                   _incu_i_range_judge(m["体重モニタ(四隅)"], 4990, 5010)),
                ],
            },
            {
                "title": "3. 動作点検（保育器モード）",
                "kind": "mixed",
                "items": [
                    _check_item(INCU_I_OPERATION_CHECK_ITEMS[0], c.get(INCU_I_OPERATION_CHECK_ITEMS[0], "---")),
                    _measured_item("湿度コントロール(表示)", "設定90% / 表示90±3%", "90±3%",
                                   f"{m['湿度コントロール(表示)']}%",
                                   _incu_i_range_judge(m["湿度コントロール(表示)"], 87, 93)),
                    _measured_item("湿度コントロール(実測)", "実測90±10%", "90±10%",
                                   f"{m['湿度コントロール(実測)']}%",
                                   _incu_i_range_judge(m["湿度コントロール(実測)"], 80, 100)),
                    _measured_item("マニュアルコントロール(表示)", "設定34℃ / 34±1℃", "34±1℃",
                                   f"{m['マニュアルコントロール(表示)']}℃",
                                   _incu_i_range_judge(m["マニュアルコントロール(表示)"], 33, 35)),
                    _measured_item("マニュアルコントロール(実測)", "設定34℃ / 34±1℃", "34±1℃",
                                   f"{m['マニュアルコントロール(実測)']}℃",
                                   _incu_i_range_judge(m["マニュアルコントロール(実測)"], 33, 35)),
                    _measured_item("サーボコントロール(表示)", "設定34℃ / 34±0.5℃", "34±0.5℃",
                                   f"{m['サーボコントロール(表示)']}℃",
                                   _incu_i_range_judge(m["サーボコントロール(表示)"], 33.5, 34.5)),
                    _measured_item("サーボコントロール(実測)", "設定34℃ / 34±0.5℃", "34±0.5℃",
                                   f"{m['サーボコントロール(実測)']}℃",
                                   _incu_i_range_judge(m["サーボコントロール(実測)"], 33.5, 34.5)),
                    _check_item(INCU_I_OPERATION_CHECK_ITEMS[1], c.get(INCU_I_OPERATION_CHECK_ITEMS[1], "---")),
                    _measured_item("酸素濃度制御(表示)", "設定40% / 40±3%", "40±3%",
                                   f"{m['酸素濃度制御(表示)']}%",
                                   _incu_i_range_judge(m["酸素濃度制御(表示)"], 37, 43)),
                    _measured_item("酸素濃度制御(実測)", "設定40% / 40±3%", "40±3%",
                                   f"{m['酸素濃度制御(実測)']}%",
                                   _incu_i_range_judge(m["酸素濃度制御(実測)"], 37, 43)),
                    _measured_item("酸素接続口1", "10L/min / 65%以上", "65%以上",
                                   f"{m['酸素接続口1(実測)']}%",
                                   "〇" if m["酸素接続口1(実測)"] >= 65 else "×"),
                    _check_item(INCU_I_OPERATION_CHECK_ITEMS[2], c.get(INCU_I_OPERATION_CHECK_ITEMS[2], "---")),
                ],
            },
            {
                "title": "4. 警報",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in INCU_I_ALARM_ITEMS],
            },
            {
                "title": "5. 電気的安全性",
                "kind": "measure",
                "items": [
                    _measured_item("接地漏れ電流(正常)", "正常200μA以下", "≤200μA",
                                   format_leakage_ua(m["接地漏れ電流(正常)"]),
                                   "〇" if leakage_ua_int(m["接地漏れ電流(正常)"]) <= 200 else "×"),
                    _measured_item("接地漏れ電流(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["接地漏れ電流(単一故障)"]),
                                   "〇" if leakage_ua_int(m["接地漏れ電流(単一故障)"]) <= 500 else "×"),
                    _measured_item("外装漏れ電流(正常)", "正常100μA以下", "≤100μA",
                                   format_leakage_ua(m["外装漏れ電流(正常)"]),
                                   "〇" if leakage_ua_int(m["外装漏れ電流(正常)"]) <= 100 else "×"),
                    _measured_item("外装漏れ電流(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["外装漏れ電流(単一故障)"]),
                                   "〇" if leakage_ua_int(m["外装漏れ電流(単一故障)"]) <= 500 else "×"),
                    _measured_item("患者漏れ電流I(正常)", "正常100μA以下", "≤100μA",
                                   format_leakage_ua(m["患者漏れ電流I(正常)"]),
                                   "〇" if leakage_ua_int(m["患者漏れ電流I(正常)"]) <= 100 else "×"),
                    _measured_item("患者漏れ電流I(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["患者漏れ電流I(単一故障)"]),
                                   "〇" if leakage_ua_int(m["患者漏れ電流I(単一故障)"]) <= 500 else "×"),
                ],
            },
        ],
    }

# ベッドサイドモニタ（生体情報モニタ）定期点検表
VSM_APPEARANCE_ITEMS = [
    "本体外観の汚れ破損",
    "NIBP中継エアホース・マンシェットの確認",
    "ECG中継ケーブル・電極リード線の確認",
    "SpO2中継コード・フィンガープローブの確認",
    "マルチコネクタの破損",
]
VSM_FUNCTION_ITEMS = [
    "電源投入",
    "セルフチェック機能",
    "ハードキー動作確認",
    "タッチキー動作確認",
    "アラームインジケーターの動作確認",
    "アラーム音鳴るか",
    "レコーダーモジュール動作",
    "時計表示",
    "不整脈解析機能ON",
    "アラーム記録設定",
    "音量(同期・アラーム)最大",
]
VSM_BATTERY_CHECK_ITEMS = [
    "バッテリでモニタが使用可能か",
    "バッテリ使用中にバッテリランプが点灯するか",
    "バッテリ交換の必要性はあるか",
]
VSM_TRANSMITTER_ITEMS = [
    "チャンネルシールの剥がれはないか",
    "モニタ付属のアンテナ線に破損がないか",
]
VSM_ECG_RESP_ITEMS = [
    "Sinus rhythm 60bpm±2",
    "Tachycardia 180bpm±2",
    "Bradycardia 20bpm±2",
    "VF/VT",
    "呼吸数 HR60bpm設定時20回±2",
    "apneaアラーム",
    "電極はずれアラーム",
]
VSM_SPO2_ITEMS = [
    "設定SpO2:70%±3(アラーム鳴るか)",
    "設定SpO2:80%±3(アラーム鳴るか)",
    "設定SpO2:98%±3(アラーム鳴るか)",
    "プローブはずれ",
]
VSM_NIBP_ITEMS = [
    "血圧測定が可能か(カフからのリークがないか)",
    "測定ボタンを2度押すと測定が中止されるか",
    "通常測定精度(±10mmHg以内) 設定120/80",
    "PVC設定測定精度(±10mmHg以内) 設定120/80",
    "安全回路動作(300mmHg過加圧時に加圧動作の停止)",
]
VSM_IBP_ITEMS = [
    "ゼロ校正",
    "設定 10mmHg±3",
    "設定 100mmHg±3",
    "設定 AO(波型波形)",
]

def _vsm_all_check_labels():
    return (
        VSM_APPEARANCE_ITEMS + VSM_FUNCTION_ITEMS + VSM_BATTERY_CHECK_ITEMS
        + VSM_TRANSMITTER_ITEMS + VSM_ECG_RESP_ITEMS + VSM_SPO2_ITEMS
        + VSM_NIBP_ITEMS + VSM_IBP_ITEMS
    )

def default_vsm_checks():
    return {label: "---" for label in _vsm_all_check_labels()}

def default_vsm_measurements():
    return {
        "バッテリサイクル数": 0,
        "接地漏れ電流(正常)": 0,
        "接地漏れ電流(単一故障)": 0,
        "外装漏れ電流(正常)": 0,
        "外装漏れ電流(単一故障)": 0,
        "患者漏れ電流I(正常)": 0,
        "患者漏れ電流I(単一故障)": 0,
    }

def default_vsm_meta():
    return {"設置病棟": "", "チャンネル番号": ""}

def _render_inspection_check_grid(items, checks, key_prefix):
    opts = INSPECTION_CHECK_OPTIONS
    c1, c2 = st.columns(2)
    for idx, label in enumerate(items):
        with (c1 if idx % 2 == 0 else c2):
            checks[label] = st.radio(
                label, opts, horizontal=True, index=None,
                key=f"{key_prefix}_{idx}",
            )

def _validate_leakage_currents(measurements, ng_items):
    m = measurements
    limits = [
        ("接地漏れ電流(正常)", m["接地漏れ電流(正常)"], 200),
        ("接地漏れ電流(単一故障)", m["接地漏れ電流(単一故障)"], 500),
        ("外装漏れ電流(正常)", m["外装漏れ電流(正常)"], 100),
        ("外装漏れ電流(単一故障)", m["外装漏れ電流(単一故障)"], 500),
        ("患者漏れ電流I(正常)", m["患者漏れ電流I(正常)"], 100),
        ("患者漏れ電流I(単一故障)", m["患者漏れ電流I(単一故障)"], 500),
    ]
    for name, val, limit in limits:
        if leakage_ua_int(val) > limit:
            ng_items.append(f"{name}（{leakage_ua_int(val)}μA）")

def render_vsm_inspection_fields(vsm_checks, vsm_measurements, vsm_meta):
    """ベッドサイドモニタ 定期点検表の入力欄"""
    st.caption(INSPECTION_CHECK_LEGEND)
    st.caption(
        "対象機種例: PVM-2701 , PVM-4761"
    )
    m1, m2 = st.columns(2)
    with m1:
        vsm_meta["設置病棟"] = st.text_input(
            "設置病棟", value=vsm_meta.get("設置病棟", ""), key="vsm_ward",
        )
    with m2:
        vsm_meta["チャンネル番号"] = st.text_input(
            "チャンネル番号", value=vsm_meta.get("チャンネル番号", ""), key="vsm_channel",
        )

    st.write("**外観点検**")
    _render_inspection_check_grid(VSM_APPEARANCE_ITEMS, vsm_checks, "vsm_app")

    st.write("**機能点検**")
    _render_inspection_check_grid(VSM_FUNCTION_ITEMS, vsm_checks, "vsm_func")

    st.write("**バッテリ**")
    st.caption("対象機器：CSM-1000, BSM-5700, PVM-4000シリーズ")
    _render_inspection_check_grid(VSM_BATTERY_CHECK_ITEMS, vsm_checks, "vsm_bat")
    b1, b2, b3 = st.columns(3)
    with b1:
        bat_date = st.date_input(
            "バッテリの製造年月日", value=date.today(), key="vsm_bat_date",
        )
        vsm_meta["バッテリ製造年月日"] = str(bat_date)
    with b2:
        vsm_measurements["バッテリサイクル数"] = st.number_input(
            "バッテリサイクル数 (回)",
            value=int(vsm_measurements.get("バッテリサイクル数", 0)),
            min_value=0, step=1, key="vsm_bat_cycle",
        )

    st.write("**送信機**")
    _render_inspection_check_grid(VSM_TRANSMITTER_ITEMS, vsm_checks, "vsm_tx")

    st.write("**ECG・RESP**")
    _render_inspection_check_grid(VSM_ECG_RESP_ITEMS, vsm_checks, "vsm_ecg")

    st.write("**SpO2**")
    _render_inspection_check_grid(VSM_SPO2_ITEMS, vsm_checks, "vsm_spo2")

    st.write("**NIBP**")
    _render_inspection_check_grid(VSM_NIBP_ITEMS, vsm_checks, "vsm_nibp")

    st.write("**IBP**")
    _render_inspection_check_grid(VSM_IBP_ITEMS, vsm_checks, "vsm_ibp")

    st.write("**漏れ電流**")
    st.caption("接地漏れ電流: 正常200μA以下 / 単一故障500μA以下")
    e1, e2 = st.columns(2)
    with e1:
        vsm_measurements["接地漏れ電流(正常)"] = _leakage_number_input(
            "接地漏れ電流 正常 (μA)", vsm_measurements["接地漏れ電流(正常)"], "vsm_earth_n",
        )
    with e2:
        vsm_measurements["接地漏れ電流(単一故障)"] = _leakage_number_input(
            "接地漏れ電流 単一故障 (μA)", vsm_measurements["接地漏れ電流(単一故障)"], "vsm_earth_f",
        )
    st.caption("外装漏れ電流: 正常100μA以下 / 単一故障500μA以下")
    e3, e4 = st.columns(2)
    with e3:
        vsm_measurements["外装漏れ電流(正常)"] = _leakage_number_input(
            "外装漏れ電流 正常 (μA)", vsm_measurements["外装漏れ電流(正常)"], "vsm_enc_n",
        )
    with e4:
        vsm_measurements["外装漏れ電流(単一故障)"] = _leakage_number_input(
            "外装漏れ電流 単一故障 (μA)", vsm_measurements["外装漏れ電流(単一故障)"], "vsm_enc_f",
        )
    st.caption("患者漏れ電流I: 正常100μA以下 / 単一故障500μA以下")
    e5, e6 = st.columns(2)
    with e5:
        vsm_measurements["患者漏れ電流I(正常)"] = _leakage_number_input(
            "患者漏れ電流I 正常 (μA)", vsm_measurements["患者漏れ電流I(正常)"], "vsm_pat_n",
        )
    with e6:
        vsm_measurements["患者漏れ電流I(単一故障)"] = _leakage_number_input(
            "患者漏れ電流I 単一故障 (μA)", vsm_measurements["患者漏れ電流I(単一故障)"], "vsm_pat_f",
        )

def build_vsm_report_sections(vsm_checks, vsm_measurements, vsm_meta):
    c = vsm_checks
    m = vsm_measurements
    meta = vsm_meta or {}
    return {
        "form": "vsm_bedside",
        "title": "ベッドサイドモニタ定期点検",
        "sections": [
            {
                "title": "点検情報",
                "kind": "check",
                "items": [
                    {"name": "設置病棟", "result": meta.get("設置病棟", ""), "judge": "-"},
                    {"name": "チャンネル番号", "result": meta.get("チャンネル番号", ""), "judge": "-"},
                ],
            },
            {
                "title": "外観点検",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_APPEARANCE_ITEMS],
            },
            {
                "title": "機能点検",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_FUNCTION_ITEMS],
            },
            {
                "title": "バッテリ",
                "kind": "mixed",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_BATTERY_CHECK_ITEMS] + [
                    _measured_item(
                        "バッテリ製造年月日", "CSM-1000/BSM-5700/PVM-4000", "-",
                        meta.get("バッテリ製造年月日", ""), "-",
                    ),
                    _measured_item(
                        "バッテリサイクル数", "回数記録", "-",
                        f"{m.get('バッテリサイクル数', 0)}回", "-",
                    ),
                ],
            },
            {
                "title": "送信機",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_TRANSMITTER_ITEMS],
            },
            {
                "title": "ECG・RESP",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_ECG_RESP_ITEMS],
            },
            {
                "title": "SpO2",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_SPO2_ITEMS],
            },
            {
                "title": "NIBP",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_NIBP_ITEMS],
            },
            {
                "title": "IBP",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in VSM_IBP_ITEMS],
            },
            {
                "title": "漏れ電流",
                "kind": "measure",
                "items": [
                    _measured_item("接地漏れ電流(正常)", "正常200μA以下", "≤200μA",
                                   format_leakage_ua(m["接地漏れ電流(正常)"]),
                                   measure_judge(leakage_ua_int(m["接地漏れ電流(正常)"]) <= 200)),
                    _measured_item("接地漏れ電流(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["接地漏れ電流(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["接地漏れ電流(単一故障)"]) <= 500)),
                    _measured_item("外装漏れ電流(正常)", "正常100μA以下", "≤100μA",
                                   format_leakage_ua(m["外装漏れ電流(正常)"]),
                                   measure_judge(leakage_ua_int(m["外装漏れ電流(正常)"]) <= 100)),
                    _measured_item("外装漏れ電流(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["外装漏れ電流(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["外装漏れ電流(単一故障)"]) <= 500)),
                    _measured_item("患者漏れ電流I(正常)", "正常100μA以下", "≤100μA",
                                   format_leakage_ua(m["患者漏れ電流I(正常)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流I(正常)"]) <= 100)),
                    _measured_item("患者漏れ電流I(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["患者漏れ電流I(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流I(単一故障)"]) <= 500)),
                ],
            },
        ],
    }

# 心電計 定期点検表
ECG_APPEARANCE_ITEMS = [
    "外観の汚れの確認",
    "外観の破損の確認",
]
ECG_POWER_CORD_ITEMS = [
    "電源コードの汚れ・破損の確認",
    "電源コードの断線の確認",
]
ECG_FUNCTION_ITEMS = [
    "電源投入時の確認",
    "システム設定のバックアップ",
    "日付、時刻の設定、確認",
    "ディスプレイ表示、LED表示の確認",
    "キー動作の確認",
    "心電図入力テスト",
    "心電図入力CALテスト",
    "誘導コード、電極リード線の確認",
    "記録品質:蛇行・片寄りがないこと",
    "記録器テスト",
    "紙切れ検出センサの動作と音の確認",
    "ネットワーク接続の確認",
    "バーコードリーダまたは二次元コードリーダの確認",
    "磁気カードリーダの確認",
    "設置およびネットワーク接続状態の再確認",
]

def _ecg_all_check_labels():
    return ECG_APPEARANCE_ITEMS + ECG_POWER_CORD_ITEMS + ECG_FUNCTION_ITEMS

def default_ecg_checks():
    return {label: "---" for label in _ecg_all_check_labels()}

def default_ecg_measurements():
    return {
        "接地漏れ電流(正常)": 0,
        "接地漏れ電流(単一故障)": 0,
        "外装漏れ電流(正常)": 0,
        "外装漏れ電流(単一故障)": 0,
        "患者漏れ電流DC(正常)": 0,
        "患者漏れ電流AC(正常)": 0,
        "患者漏れ電流DC(単一故障)": 0,
        "患者漏れ電流AC(単一故障)": 0,
    }

def _validate_ecg_measurements(measurements, ng_items):
    m = measurements
    limits = [
        ("接地漏れ電流(正常)", m["接地漏れ電流(正常)"], 200),
        ("接地漏れ電流(単一故障)", m["接地漏れ電流(単一故障)"], 500),
        ("外装漏れ電流(正常)", m["外装漏れ電流(正常)"], 100),
        ("外装漏れ電流(単一故障)", m["外装漏れ電流(単一故障)"], 500),
        ("患者漏れ電流DC(正常)", m["患者漏れ電流DC(正常)"], 10),
        ("患者漏れ電流AC(正常)", m["患者漏れ電流AC(正常)"], 10),
        ("患者漏れ電流DC(単一故障)", m["患者漏れ電流DC(単一故障)"], 50),
        ("患者漏れ電流AC(単一故障)", m["患者漏れ電流AC(単一故障)"], 50),
    ]
    for name, val, limit in limits:
        if leakage_ua_int(val) > limit:
            ng_items.append(f"{name}（{leakage_ua_int(val)}μA）")

def render_ecg_inspection_fields(ecg_checks, ecg_measurements):
    """心電計 定期点検表の入力欄"""
    st.caption(INSPECTION_CHECK_LEGEND)

    st.write("**1. 外観の汚れ・破損の確認**")
    _render_inspection_check_grid(ECG_APPEARANCE_ITEMS, ecg_checks, "ecg_app")

    st.write("**2. 電源コードの確認**")
    _render_inspection_check_grid(ECG_POWER_CORD_ITEMS, ecg_checks, "ecg_pwr")

    st.write("**3. 機能点検**")
    _render_inspection_check_grid(ECG_FUNCTION_ITEMS, ecg_checks, "ecg_func")

    st.write("**4. 安全性のチェック（漏れ電流）**")
    st.caption("接地漏れ電流: 正常200μA以下 / 単一故障500μA以下")
    s1, s2 = st.columns(2)
    with s1:
        ecg_measurements["接地漏れ電流(正常)"] = _leakage_number_input(
            "接地漏れ電流 正常 (μA)", ecg_measurements["接地漏れ電流(正常)"], "ecg_earth_n",
        )
    with s2:
        ecg_measurements["接地漏れ電流(単一故障)"] = _leakage_number_input(
            "接地漏れ電流 単一故障 (μA)", ecg_measurements["接地漏れ電流(単一故障)"], "ecg_earth_f",
        )
    st.caption("外装漏れ電流(外装-大地): 正常100μA以下 / 単一故障500μA以下")
    s3, s4 = st.columns(2)
    with s3:
        ecg_measurements["外装漏れ電流(正常)"] = _leakage_number_input(
            "外装漏れ電流 正常 (μA)", ecg_measurements["外装漏れ電流(正常)"], "ecg_enc_n",
        )
    with s4:
        ecg_measurements["外装漏れ電流(単一故障)"] = _leakage_number_input(
            "外装漏れ電流 単一故障 (μA)", ecg_measurements["外装漏れ電流(単一故障)"], "ecg_enc_f",
        )
    st.caption("患者漏れ電流(患者接続部-大地): 正常DC/AC各10μA以下 / 単一故障DC/AC各50μA以下")
    s5, s6, s7, s8 = st.columns(4)
    with s5:
        ecg_measurements["患者漏れ電流DC(正常)"] = _leakage_number_input(
            "患者漏れ DC 正常 (μA)", ecg_measurements["患者漏れ電流DC(正常)"], "ecg_pat_dc_n",
        )
    with s6:
        ecg_measurements["患者漏れ電流AC(正常)"] = _leakage_number_input(
            "患者漏れ AC 正常 (μA)", ecg_measurements["患者漏れ電流AC(正常)"], "ecg_pat_ac_n",
        )
    with s7:
        ecg_measurements["患者漏れ電流DC(単一故障)"] = _leakage_number_input(
            "患者漏れ DC 単一故障 (μA)", ecg_measurements["患者漏れ電流DC(単一故障)"], "ecg_pat_dc_f",
        )
    with s8:
        ecg_measurements["患者漏れ電流AC(単一故障)"] = _leakage_number_input(
            "患者漏れ AC 単一故障 (μA)", ecg_measurements["患者漏れ電流AC(単一故障)"], "ecg_pat_ac_f",
        )

def build_ecg_report_sections(ecg_checks, ecg_measurements):
    c = ecg_checks
    m = ecg_measurements
    return {
        "form": "ecg_monitor",
        "title": "心電計 定期点検表",
        "sections": [
            {
                "title": "1. 外観の汚れ・破損の確認",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in ECG_APPEARANCE_ITEMS],
            },
            {
                "title": "2. 電源コードの確認",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in ECG_POWER_CORD_ITEMS],
            },
            {
                "title": "3. 機能点検",
                "kind": "check",
                "items": [_check_item(l, c.get(l, "---")) for l in ECG_FUNCTION_ITEMS],
            },
            {
                "title": "4. 安全性のチェック（漏れ電流）",
                "kind": "measure",
                "items": [
                    _measured_item("接地漏れ電流(正常)", "正常200μA以下", "≤200μA",
                                   format_leakage_ua(m["接地漏れ電流(正常)"]),
                                   measure_judge(leakage_ua_int(m["接地漏れ電流(正常)"]) <= 200)),
                    _measured_item("接地漏れ電流(単一故障)", "単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["接地漏れ電流(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["接地漏れ電流(単一故障)"]) <= 500)),
                    _measured_item("外装漏れ電流(正常)", "外装-大地 正常100μA以下", "≤100μA",
                                   format_leakage_ua(m["外装漏れ電流(正常)"]),
                                   measure_judge(leakage_ua_int(m["外装漏れ電流(正常)"]) <= 100)),
                    _measured_item("外装漏れ電流(単一故障)", "外装-大地 単一故障500μA以下", "≤500μA",
                                   format_leakage_ua(m["外装漏れ電流(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["外装漏れ電流(単一故障)"]) <= 500)),
                    _measured_item("患者漏れ電流DC(正常)", "患者接続部-大地 DC正常10μA以下", "≤10μA",
                                   format_leakage_ua(m["患者漏れ電流DC(正常)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流DC(正常)"]) <= 10)),
                    _measured_item("患者漏れ電流AC(正常)", "患者接続部-大地 AC正常10μA以下", "≤10μA",
                                   format_leakage_ua(m["患者漏れ電流AC(正常)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流AC(正常)"]) <= 10)),
                    _measured_item("患者漏れ電流DC(単一故障)", "DC単一故障50μA以下", "≤50μA",
                                   format_leakage_ua(m["患者漏れ電流DC(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流DC(単一故障)"]) <= 50)),
                    _measured_item("患者漏れ電流AC(単一故障)", "AC単一故障50μA以下", "≤50μA",
                                   format_leakage_ua(m["患者漏れ電流AC(単一故障)"]),
                                   measure_judge(leakage_ua_int(m["患者漏れ電流AC(単一故障)"]) <= 50)),
                ],
            },
        ],
    }

JST = ZoneInfo("Asia/Tokyo")

def now_jst():
    return datetime.now(JST)

def format_jst(dt=None, fmt="%Y-%m-%d %H:%M:%S"):
    return (dt or now_jst()).strftime(fmt)

LEGACY_ME_COLUMNS = ("旧番号", "旧管理番号")

INSPECTION_HISTORY_COLUMNS = [
    "点検日", "管理番号", "カテゴリ", "シリアルNo", "製造年月日", "機種",
    "実施者", "判定", "詳細データ", "備考",
]

TEPRA_IOS_STORE = "https://apps.apple.com/jp/app/tepra-link-2/id1614816445"
TEPRA_ANDROID_STORE = "https://play.google.com/store/apps/details?id=jp.co.kingjim.android.tepra2"

_run_cookie_manager = None

def get_cookie_manager():
    """CookieManager は1実行につき1回だけ生成（session_state 保存は不可）"""
    global _run_cookie_manager
    if _run_cookie_manager is None:
        _run_cookie_manager = stx.CookieManager(key="miratech_cookie_manager")
    return _run_cookie_manager

def read_browser_cookies():
    """__init__ で読み込んだ cookies を返す（get_all の二重呼び出しを避ける）"""
    cookies = get_cookie_manager().cookies
    if cookies is None:
        return None
    return cookies if isinstance(cookies, dict) else {}

AUTH_COOKIE_NAME = "miratech_auth"
LAST_ACTIVE_COOKIE = "miratech_last_active"
IDLE_HOURS = 5
IDLE_SECONDS = IDLE_HOURS * 3600
SESSION_MAX_AGE_DAYS = 30

def display_dataframe(df, **kwargs):
    """Cloud 上の pyarrow segfault 回避のため文字列型に統一して表示"""
    if kwargs.pop("use_container_width", None):
        kwargs.setdefault("width", "stretch")
    if df is None or df.empty:
        return st.dataframe(df, **kwargs)
    return st.dataframe(_sanitize_dataframe(df), **kwargs)

def _normalize_spreadsheet_id(raw):
    s = str(raw).strip().strip('"').strip("'")
    if "/spreadsheets/d/" in s:
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
        if match:
            return match.group(1)
    return s

def _load_gsheets_settings():
    gs = st.secrets["connections"]["gsheets"]
    spreadsheet_id = _normalize_spreadsheet_id(gs.get("spreadsheet", ""))
    config = dict(gs["configuration"])
    pk = config.get("private_key", "")
    if "\\n" in pk:
        config["private_key"] = pk.replace("\\n", "\n")
    return spreadsheet_id, config, config.get("client_email", "")

def _validate_spreadsheet_id(spreadsheet_id):
    if not spreadsheet_id:
        raise ValueError("Secrets に spreadsheet ID が設定されていません。[connections.gsheets] の spreadsheet を確認してください。")
    if len(spreadsheet_id) < 20:
        raise ValueError(f"spreadsheet ID の形式が不正です: {spreadsheet_id!r}")

class SheetReadError(Exception):
    """スプレッドシート読み込み失敗"""

@st.cache_resource
def _get_sheet_client():
    spreadsheet_id, config, _ = _load_gsheets_settings()
    client = gspread.service_account_from_dict(config)
    return client, spreadsheet_id

@st.cache_data(ttl=15, show_spinner=False)
def _cached_sheet_read(worksheet_name):
    client, spreadsheet_id = _get_sheet_client()
    _validate_spreadsheet_id(spreadsheet_id)
    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)
    return get_as_dataframe(ws, evaluate_formulas=True)

class SheetConn:
    """gspread 直結（duckdb 不使用・Cloud segfault 回避）"""

    def read(self, worksheet=None, ttl=15, **kwargs):
        df = _cached_sheet_read(worksheet)
        return df if df is not None else pd.DataFrame()

    def update(self, worksheet=None, data=None, **kwargs):
        st.cache_data.clear()
        client, spreadsheet_id = _get_sheet_client()
        ws = client.open_by_key(spreadsheet_id).worksheet(worksheet)
        write_df = data.fillna("") if data is not None else pd.DataFrame()
        set_with_dataframe(
            ws, write_df,
            include_index=False, include_column_header=True, resize=True,
        )

@st.cache_resource
def get_sheet_conn():
    return SheetConn()

def _get_gemini_api_key():
    """Streamlit Secrets / 環境変数から Gemini API キーを取得"""
    candidates = []
    try:
        candidates.append(st.secrets.get("GEMINI_API_KEY"))
    except Exception:
        pass
    try:
        gemini = st.secrets.get("gemini")
        if isinstance(gemini, dict):
            candidates.append(gemini.get("api_key"))
            candidates.append(gemini.get("GEMINI_API_KEY"))
    except Exception:
        pass
    candidates.append(os.environ.get("GEMINI_API_KEY"))

    for raw in candidates:
        if raw is None:
            continue
        key = str(raw).strip().strip('"').strip("'")
        if key:
            return key
    return ""

def _gemini_key_status_message():
    if _get_gemini_api_key():
        return "Gemini API Key: 設定済み"
    return "Gemini API Key: 未設定"

def _get_gemini_model():
    try:
        model = st.secrets.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
    except Exception:
        model = "gemini-2.0-flash-lite"
    model = str(model or "gemini-2.0-flash-lite").strip()
    return model or "gemini-2.0-flash-lite"

def _sanitize_api_error_message(message):
    return re.sub(r"key=AIza[^\s&\"']+", "key=***", str(message))

def _prepare_nameplate_image(image_bytes, max_side=768):
    """送信サイズを抑えてアップロード・解析を高速化"""
    img = Image.open(BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=70, optimize=True)
    return buf.getvalue(), "image/jpeg"

def _image_fingerprint(image_bytes):
    return hashlib.sha256(image_bytes).hexdigest()

def analyze_nameplate_with_gemini(image_bytes, mime_type="image/jpeg"):
    """Gemini REST API で銘板画像を解析（gRPC ライブラリを使わず Cloud でも安全）"""
    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    image_bytes, mime_type = _prepare_nameplate_image(image_bytes)

    prompt = (
        "医療機器の銘板画像から model, serial_number, manufacture_year を"
        " JSON だけで返してください。"
    )
    model = _get_gemini_model()
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
            ]
        }]
    }

    resp = requests.post(url, json=payload, timeout=35)
    if resp.status_code == 429:
        raise ValueError(
            "Gemini API の利用上限に達しました。1〜2分待ってから再度"
            "「AIで銘板を読み取る」を押してください。"
        )
    if resp.status_code == 400:
        raise ValueError(f"Gemini API リクエストエラー: {resp.text[:300]}")
    if resp.status_code in (401, 403):
        raise ValueError(
            "Gemini API Key が無効です。Streamlit Cloud の Secrets の "
            "GEMINI_API_KEY を Google AI Studio で発行したキーに差し替えてください。"
        )
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ValueError(_sanitize_api_error_message(str(e))) from e
    body = resp.json()
    return body["candidates"][0]["content"]["parts"][0]["text"]

@st.fragment
def _render_ai_nameplate_scanner():
    """カメラ・AI解析のみ部分再実行（ページ全体の再読込を避ける）"""
    if not _get_gemini_api_key():
        st.error(
            "AI銘板スキャナーを使うには、Streamlit Cloud の Secrets に "
            "GEMINI_API_KEY を追加してください。"
        )
        st.code(
            'GEMINI_API_KEY = "AIzaSy..."\n\n[connections.gsheets]\nspreadsheet = "..."',
            language="toml",
        )
        return

    st.caption(_gemini_key_status_message())
    capture_mode = st.radio(
        "写真の取り込み",
        ["ファイルを選択", "カメラで撮影"],
        horizontal=True,
        key="ai_capture_mode",
        help="ファイル選択の方が表示が速い端末があります。",
    )

    img_file = None
    if capture_mode == "カメラで撮影":
        img_file = st.camera_input("銘板を撮影", key="ai_camera_native")
    else:
        st.caption("スマホは「ファイルを選択」→「写真を撮る」でカメラが使えます。")
        img_file = st.file_uploader(
            "銘板写真",
            type=["jpg", "jpeg", "png", "webp"],
            key="ai_file_uploader",
        )

    if img_file is None:
        return

    current_image_bytes = img_file.getvalue()
    image_fp = _image_fingerprint(current_image_bytes)
    if st.session_state.get("scan_image_fp") != image_fp:
        st.session_state["scan_image_fp"] = image_fp
        st.session_state.pop("scan_model", None)
        st.session_state.pop("scan_sn", None)
        st.session_state.pop("scan_year", None)
        st.session_state.pop("scan_error", None)
        st.session_state["scan_ready"] = True

    st.image(current_image_bytes, caption="選択中の写真", width=240)
    st.caption("写真を確認してから、下のボタンで AI 読み取りを開始してください。")
    if st.session_state.get("scan_error"):
        st.error(st.session_state["scan_error"])

    run_scan = st.button(
        "AIで銘板を読み取る",
        type="primary",
        use_container_width=True,
        key="run_nameplate_scan",
        disabled=not st.session_state.get("scan_ready", False),
    )
    if not (run_scan and st.session_state.get("scan_ready")):
        return

    st.session_state["scan_ready"] = False
    with st.spinner("AIが文字を解析しています..."):
        try:
            response_text = analyze_nameplate_with_gemini(current_image_bytes)
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                st.session_state["scan_model"] = data.get("model", "")
                st.session_state["scan_sn"] = data.get("serial_number", "")
                st.session_state["scan_year"] = data.get("manufacture_year", "")
                st.session_state.pop("scan_error", None)
                st.session_state["last_scanned_image"] = current_image_bytes
                try:
                    st.rerun(scope="app")
                except TypeError:
                    st.rerun()
            else:
                st.session_state["scan_error"] = (
                    "文字が見つかりませんでした。明るい場所で再度撮影し、"
                    "もう一度「AIで銘板を読み取る」を押してください。"
                )
                st.session_state["scan_ready"] = True
        except Exception as e:
            st.session_state["scan_error"] = _sanitize_api_error_message(str(e))
            st.session_state["scan_ready"] = True

st.set_page_config(page_title="miratech 医療機器管理システム", layout="centered")

def _inject_pc_unified_layout():
    """タブレットでも PC と同じメイン幅・余白になるよう CSS で統一"""
    st.markdown(
        """
        <style>
        section.main div.block-container,
        div.stMainBlockContainer {
            max-width: 46rem !important;
            padding-top: 2rem !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        [data-testid="stSidebar"] > div:first-child {
            width: 16rem !important;
            min-width: 16rem !important;
        }
        /* 機器検索結果（disabled 入力）を濃く表示 */
        div[data-testid="stTextInput"] input:disabled {
            -webkit-text-fill-color: #111827 !important;
            color: #111827 !important;
            opacity: 1 !important;
            background-color: #e5e7eb !important;
            border: 1px solid #6b7280 !important;
            font-weight: 700 !important;
        }
        div[data-testid="stTextInput"] label,
        div[data-testid="stTextInput"] [data-testid="stWidgetLabel"],
        div[data-testid="stTextInput"] [data-testid="stWidgetLabel"] p {
            color: #1f2937 !important;
            font-weight: 600 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

_inject_pc_unified_layout()

# データお掃除用の共通関数
def clean_data_str(val):
    s = str(val).replace("'", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.lower() == "nan":
        s = ""
    return s

def normalize_stored_model(category, stored_model):
    """機種列を型式のみに解釈（旧形式 カテゴリ(型式) にも対応）"""
    raw = clean_data_str(stored_model)
    if not raw:
        return ""
    cat = clean_data_str(category)
    if cat:
        legacy_prefix = f"{cat}("
        if raw.startswith(legacy_prefix) and raw.endswith(")"):
            return raw[len(legacy_prefix):-1]
    paren = re.match(r"^(.+)\(([^)]+)\)$", raw)
    if paren:
        return clean_data_str(paren.group(2))
    return raw

def model_for_spreadsheet(device_model):
    """スプレッドシート「機種」列に保存する値（型式のみ）"""
    return clean_data_str(device_model)

def _sanitize_dataframe(df):
    """PyArrow 型の DataFrame が duckdb/Streamlit Cloud で segfault するのを防ぐ"""
    if df is None or df.empty:
        return df
    preserve_cols = {"詳細データ", "備考"}
    clean = df.copy()
    for col in clean.columns:
        if col in preserve_cols:
            clean[col] = clean[col].apply(
                lambda v: "" if pd.isna(v) else str(v).strip() if str(v).strip().lower() != "nan" else "",
            )
        else:
            clean[col] = clean[col].apply(lambda v: "" if pd.isna(v) else clean_data_str(v))
    return clean

# 通信エラー対策：安全にスプレッドシートを読み込むためのリトライ関数
def safe_read_worksheet(conn, worksheet_name, default_columns=None, raise_on_fail=False):
    last_error = None
    for i in range(3):
        try:
            df = conn.read(worksheet=worksheet_name, ttl=15)
            if df is not None:
                return _sanitize_dataframe(df.dropna(how="all").fillna(""))
        except Exception as e:
            last_error = e
            if i < 2:
                time.sleep(1)
    err_msg = str(last_error) if last_error else "不明なエラー"
    spreadsheet_id, _, service_email = _load_gsheets_settings()
    if "PEM" in err_msg or "private_key" in err_msg.lower():
        hint = "Secrets の private_key が壊れています。Google Cloud から JSON を再ダウンロードして貼り直してください。"
    elif "404" in err_msg or "SpreadsheetNotFound" in err_msg:
        hint = (
            "共有は設定済みでも 404 になる場合、Secrets の spreadsheet ID が"
            " 今開いているスプレッドシートと一致していないことが多いです。"
            " ブラウザの URL の /d/ と /edit の間の ID と Secrets を照合してください。"
            " 設定変更後は Streamlit Cloud で「Reboot app」を実行してください。"
        )
    elif "403" in err_msg or "Permission" in err_msg:
        hint = f"権限不足です。{service_email} をスプレッドシートの「編集者」に追加してください。"
    elif "Worksheet" in err_msg:
        hint = f"シート名「{worksheet_name}」がスプレッドシート内にありません。"
    else:
        hint = "通信環境または Secrets 設定を確認してください。"
    st.error(f"スプレッドシート（{worksheet_name}）の読み込みに失敗しました。{hint}")
    st.caption(f"詳細: {err_msg}")
    st.caption(f"接続先 ID: {spreadsheet_id} / アカウント: {service_email}")
    st.cache_data.clear()
    st.cache_resource.clear()
    if raise_on_fail:
        raise SheetReadError(err_msg)
    return pd.DataFrame(columns=default_columns) if default_columns else pd.DataFrame()

def clean_series(series):
    return series.astype(str).str.replace("'", "", regex=False).str.replace(r'\.0$', '', regex=True).str.replace(r'^nan$', '', flags=re.IGNORECASE, regex=True).str.strip()

# ゼロ落ち防止用の関数
def protect_zeros(val_str):
    val_str = str(val_str).strip()
    if val_str.startswith("0") and val_str.isdigit():
        return f"'{val_str}"
    return val_str

def normalize_inspection_history_df(df):
    """点検履歴シートを標準列構成に揃える（不足列は空で補完）"""
    if df is None or df.empty:
        return pd.DataFrame(columns=INSPECTION_HISTORY_COLUMNS)
    out = df.copy()
    for col in INSPECTION_HISTORY_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out

def append_inspection_history_row(existing_history, new_hist_row):
    """点検履歴へ1行追加（管理番号・シリアルNo が欠落しないよう列を統一）"""
    existing = normalize_inspection_history_df(existing_history)
    row = {col: new_hist_row.get(col, "") for col in INSPECTION_HISTORY_COLUMNS}
    if row.get("管理番号"):
        row["管理番号"] = protect_zeros(clean_data_str(row["管理番号"]))
    if row.get("シリアルNo"):
        row["シリアルNo"] = protect_zeros(clean_data_str(row["シリアルNo"]))
    new_df = pd.DataFrame([row])
    extra_cols = [c for c in existing.columns if c not in INSPECTION_HISTORY_COLUMNS]
    combined_cols = list(dict.fromkeys(list(INSPECTION_HISTORY_COLUMNS) + extra_cols))
    for col in combined_cols:
        if col not in new_df.columns:
            new_df[col] = ""
        if col not in existing.columns:
            existing[col] = ""
    return pd.concat([existing[combined_cols], new_df[combined_cols]], ignore_index=True)

def _device_match_tokens(target_me, master_row=None):
    """機器照合に使う管理番号・旧番号の集合"""
    tokens = set()
    me = clean_data_str(target_me)
    if me:
        tokens.add(me)
    if master_row is not None:
        for legacy in _legacy_numbers_from_row(master_row):
            tokens.add(legacy)
    return tokens

def build_device_qr_url(me_no):
    clean_url = APP_URL.rstrip("/")
    return f"{clean_url}/?me_no={clean_data_str(me_no)}"

def generate_qr_png_bytes(url):
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def _legacy_numbers_from_row(row):
    """行から旧番号リストを取得（カンマ・スラッシュ区切り複数可）"""
    numbers = []
    for col in LEGACY_ME_COLUMNS:
        raw = clean_data_str(row.get(col, ""))
        if not raw:
            continue
        for part in re.split(r"[,、/|]+", raw):
            token = clean_data_str(part)
            if token:
                numbers.append(token)
    return numbers

def find_device_row(df_master, keyword):
    """管理番号・旧番号・シリアルNo で機器マスター行を検索"""
    if df_master is None or df_master.empty or "管理番号" not in df_master.columns:
        return None, None

    clean_kw = clean_data_str(keyword)
    if not clean_kw:
        return None, None

    clean_db_me = clean_series(df_master["管理番号"])
    matched = df_master[clean_db_me == clean_kw]
    if not matched.empty:
        return matched.iloc[0], "管理番号"

    for _, row in df_master.iterrows():
        if clean_kw in _legacy_numbers_from_row(row):
            return row, "旧番号"

    if "シリアルNo" in df_master.columns:
        clean_db_sn = clean_series(df_master["シリアルNo"])
        matched = df_master[clean_db_sn == clean_kw]
        if not matched.empty:
            return matched.iloc[0], "シリアルNo"

    return None, None

def lookup_device_for_sticker(df_master, me_no):
    row, match_type = find_device_row(df_master, me_no)
    if row is None:
        return {}
    current_me = clean_data_str(row.get("管理番号", ""))
    return {
        "model_name": normalize_stored_model(row.get("カテゴリ", ""), row.get("機種", "")),
        "me_no": current_me,
        "serial_no": clean_data_str(row.get("シリアルNo", "")),
        "delivery_date": clean_data_str(row.get("購入日", "") or row.get("納入日", "") or row.get("納品日", "")),
        "legacy_me": clean_data_str(row.get("旧番号", "") or row.get("旧管理番号", "")),
        "matched_via": match_type,
    }

def apply_sticker_master_lookup(me_no, master_info):
    """管理番号変更時、key 付き text_input の session_state にマスター値を反映"""
    lookup_key = clean_data_str(me_no)
    if not lookup_key:
        st.session_state.pop("_sticker_lookup_me", None)
        return
    if st.session_state.get("_sticker_lookup_me") == lookup_key:
        return
    st.session_state["_sticker_lookup_me"] = lookup_key
    if master_info:
        st.session_state["sticker_me_display"] = master_info.get("me_no", lookup_key)
        st.session_state["sticker_model"] = master_info.get("model_name", "")
        st.session_state["sticker_serial"] = master_info.get("serial_no", "")
        st.session_state["sticker_delivery"] = master_info.get("delivery_date", "")
    else:
        st.session_state["sticker_me_display"] = lookup_key

def sync_device_display_fields(prefix, me_no, category, serial, model):
    """機器検索結果が変わったら、表示用 text_input の session_state を更新"""
    lookup_key = clean_data_str(me_no)
    track_key = f"_{prefix}_lookup_me"
    if not lookup_key:
        st.session_state.pop(track_key, None)
        return
    if st.session_state.get(track_key) == lookup_key:
        return
    st.session_state[track_key] = lookup_key
    st.session_state[f"{prefix}_disp_me"] = lookup_key
    st.session_state[f"{prefix}_disp_cat"] = clean_data_str(category)
    st.session_state[f"{prefix}_disp_sn"] = clean_data_str(serial)
    st.session_state[f"{prefix}_disp_model"] = clean_data_str(model)

def render_management_sticker(model_name, me_no, serial_no, delivery_date, qr_url=None):
    if not qr_url:
        qr_url = build_device_qr_url(me_no)
    qr_b64 = base64.b64encode(generate_qr_png_bytes(qr_url)).decode()
    sticker_html = f"""
    <div class="mgmt-sticker" style="
        border: 2px solid #222; padding: 10px 12px; max-width: 440px;
        font-family: 'Helvetica Neue', Arial, sans-serif; background: #fff; color: #000;
    ">
        <div style="display: flex; align-items: center; gap: 14px;">
            <div style="flex: 1; font-size: 14px; line-height: 1.65; word-break: break-word;">
                <div><b>機種名：</b>{html.escape(clean_data_str(model_name))}</div>
                <div><b>管理番号：</b>{html.escape(clean_data_str(me_no))}</div>
                <div><b>シリアル：</b>{html.escape(clean_data_str(serial_no))}</div>
                <div><b>購入日：</b>{html.escape(clean_data_str(delivery_date))}</div>
            </div>
            <div style="flex-shrink: 0; text-align: center;">
                <img src="data:image/png;base64,{qr_b64}" width="96" height="96" alt="QRコード">
            </div>
        </div>
    </div>
    """
    st.markdown(sticker_html, unsafe_allow_html=True)

def render_tepra_print_button(copy_text, button_key="tepra_print"):
    """QR用URLをクリップボードにコピー（TEPRA Link 2 は手動起動）"""
    js_text = json.dumps(copy_text)

    components.html(
        f"""
        <div style="font-family: sans-serif; max-width: 100%;">
            <button id="{button_key}" type="button" style="
                width: 100%; padding: 14px 16px; font-size: 16px; font-weight: 700;
                background: #0068c9; color: #fff; border: none; border-radius: 10px;
                cursor: pointer; margin-top: 4px;
            ">QR用URLをコピー</button>
            <p id="{button_key}_msg" style="
                font-size: 13px; color: #0068c9; margin: 8px 0 0; display: none; font-weight: 700;
            ">URLをコピーしました。TEPRA Link 2 アプリを開いて貼り付けてください。</p>
        </div>
        <script>
        (function() {{
            var copyText = {js_text};
            var btn = document.getElementById("{button_key}");
            var msg = document.getElementById("{button_key}_msg");
            btn.addEventListener("click", function() {{
                function onCopied() {{
                    msg.style.display = "block";
                }}
                function fallbackCopy() {{
                    var ta = document.createElement("textarea");
                    ta.value = copyText;
                    ta.style.position = "fixed";
                    ta.style.left = "-9999px";
                    document.body.appendChild(ta);
                    ta.focus();
                    ta.select();
                    try {{ document.execCommand("copy"); }} catch (e) {{}}
                    document.body.removeChild(ta);
                    onCopied();
                }}
                if (navigator.clipboard && navigator.clipboard.writeText) {{
                    navigator.clipboard.writeText(copyText).then(onCopied).catch(fallbackCopy);
                }} else {{
                    fallbackCopy();
                }}
            }});
        }})();
        </script>
        """,
        height=95,
    )
    st.info(
        "TEPRA Link 2 はブラウザから直接起動できません。"
        " URLをコピーしたあと、端末のホーム画面から TEPRA Link 2 を開いてください。"
    )
    col_ios, col_android = st.columns(2)
    with col_ios:
        st.link_button("iPhone/iPad: App Store", url=TEPRA_IOS_STORE, use_container_width=True)
    with col_android:
        st.link_button("Android: Google Play", url=TEPRA_ANDROID_STORE, use_container_width=True)

def render_sticker_workflow(model_name, me_no, serial_no, delivery_date, button_key="tepra_print"):
    qr_url = build_device_qr_url(me_no)
    st.markdown("#### 管理番号シール プレビュー")
    render_management_sticker(model_name, me_no, serial_no, delivery_date, qr_url)
    render_tepra_print_button(qr_url, button_key=button_key)
    with st.expander("TEPRA Link 2 での操作手順"):
        st.markdown(
            "1. **QR用URLをコピー** をタップ\n"
            "2. ホーム画面から **TEPRA Link 2** アプリを開く\n"
            "3. **新規ラベル → QRコード** を選択\n"
            "4. テキスト欄で **貼り付け（ペースト）**\n"
            "5. ラベル幅 18mm 以上を推奨（公式マニュアル）\n"
            "6. **印刷** をタップ\n\n"
            "アプリ未インストールの場合は、上の App Store / Google Play リンクから入手してください。"
        )

def parse_detail_text_to_table(detail_text):
    """詳細データ文字列を点検項目テーブル（項目名・結果・判定）に変換"""
    item_names, item_results, item_judges = [], [], []
    if not detail_text or str(detail_text).strip().lower() in ("", "nan"):
        return item_names, item_results, item_judges

    skip_keys = {"点検区分", "基準流量", "基準閉塞"}
    for p in str(detail_text).split("|"):
        p = p.strip()
        if not p or ":" not in p:
            continue
        k, v = p.split(":", 1)
        k = k.strip()
        v = v.strip()
        if k in skip_keys:
            continue
        item_names.append(k)
        if "(" in v and v.endswith(")"):
            val, jdg = v.rsplit("(", 1)
            item_results.append(val.strip())
            item_judges.append(jdg.replace(")", "").strip())
        else:
            item_results.append(v)
            item_judges.append(v)

    if not item_names and "【故障修理後点検】" in str(detail_text):
        body = str(detail_text).replace("【故障修理後点検】", "").strip()
        for segment in re.split(r"\s*/\s*", body):
            segment = segment.strip()
            if not segment or ":" not in segment:
                continue
            k, v = segment.split(":", 1)
            item_names.append(k.strip())
            item_results.append(v.strip())
            item_judges.append("-")

    return item_names, item_results, item_judges

INSPECTION_DETAIL_JSON_VERSION = 2

def _check_item(name, val):
    val_s = normalize_check_symbol(val) or "---"
    return {"name": name, "result": val_s, "judge": val_s}

def _measured_item(name, note, standard, result, judge):
    return {
        "name": name,
        "note": note,
        "standard": standard,
        "result": result,
        "judge": judge,
    }

def build_infusion_pump_report_sections(chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                        infusion_pump_checks, flow_acc, occ_press,
                                        min_flow, max_flow, min_press, max_press,
                                        flow_unit, press_unit, bubble_ad_water, bubble_ad_dry):
    """輸液ポンプ点検フォームと同一構成の印刷用セクションデータ"""
    infusion_pump_checks = infusion_pump_checks or {}
    appearance_vals = [chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7]
    flow_judge = measure_judge(min_flow <= flow_acc <= max_flow)
    press_judge = measure_judge(min_press <= occ_press <= max_press)
    water_judge = measure_judge(bubble_ad_water >= 100)
    dry_judge = measure_judge(bubble_ad_dry <= 10)

    return {
        "form": "infusion_pump",
        "sections": [
            {
                "title": "1. 外観・作動点検",
                "kind": "check",
                "items": [
                    _check_item(label, val)
                    for label, val in zip(INFUSION_PUMP_APPEARANCE_ITEMS, appearance_vals)
                ],
            },
            {
                "title": "2. 警報・作動点検",
                "kind": "check",
                "items": [
                    _check_item(label, infusion_pump_checks.get(label, "---"))
                    for label in INFUSION_PUMP_ALARM_ITEMS
                ],
            },
            {
                "title": "3. 機能・設定点検",
                "kind": "check",
                "items": [
                    _check_item(label, infusion_pump_checks.get(label, "---"))
                    for label in INFUSION_PUMP_FUNCTION_ITEMS
                ],
            },
            {
                "title": "4. 数値・精度チェック",
                "kind": "measure",
                "items": [
                    _measured_item(
                        "流量精度",
                        "※流量120ml/hr・10min・予定量20ml・許容範囲18～22ml",
                        f"{min_flow} ～ {max_flow} {flow_unit}",
                        f"{flow_acc} {flow_unit}",
                        flow_judge,
                    ),
                    _measured_item(
                        "閉塞検出",
                        "※流量120ml/h・「M」30～90kPa",
                        f"{min_press} ～ {max_press} {press_unit}",
                        f"{occ_press} {press_unit}",
                        press_judge,
                    ),
                    {
                        "name": "気泡センサーAD値",
                        "note": "※水入り輸液セット100以上・水無し輸液セット10以下",
                        "sub_items": [
                            _measured_item("水入り", "", "100以上", str(bubble_ad_water), water_judge),
                            _measured_item("水無し", "", "10以下", str(bubble_ad_dry), dry_judge),
                        ],
                    },
                ],
            },
        ],
    }

def build_syringe_pump_report_sections(chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                       flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                                       flow_unit, press_unit):
    appearance_vals = [chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7]
    flow_judge = measure_judge(min_flow <= flow_acc <= max_flow)
    press_judge = measure_judge(min_press <= occ_press <= max_press)
    return {
        "form": "syringe_pump",
        "sections": [
            {
                "title": "1. 外観・作動点検",
                "kind": "check",
                "items": [
                    _check_item(label, val)
                    for label, val in zip(INFUSION_PUMP_APPEARANCE_ITEMS, appearance_vals)
                ],
            },
            {
                "title": "2. 数値・精度チェック",
                "kind": "measure",
                "items": [
                    _measured_item(
                        "流量精度",
                        f"基準値：{min_flow} ～ {max_flow} {flow_unit}",
                        f"{min_flow} ～ {max_flow} {flow_unit}",
                        f"{flow_acc} {flow_unit}",
                        flow_judge,
                    ),
                    _measured_item(
                        "閉塞検出",
                        f"基準値：{min_press} ～ {max_press} {press_unit}",
                        f"{min_press} ～ {max_press} {press_unit}",
                        f"{occ_press} {press_unit}",
                        press_judge,
                    ),
                ],
            },
        ],
    }

def build_inspection_report_sections(check_type, device_category, inc_o_checks,
                                     chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                     flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                                     flow_unit, press_unit,
                                     infusion_pump_checks=None,
                                     bubble_ad_water=0.0, bubble_ad_dry=0.0,
                                     device_model="",
                                     incu_i_checks=None,
                                     incu_i_measurements=None,
                                     vsm_checks=None,
                                     vsm_measurements=None,
                                     vsm_meta=None,
                                     ecg_checks=None,
                                     ecg_measurements=None):
    if check_type != "院内点検(miratech)":
        return None
    if device_category == "輸液ポンプ":
        return build_infusion_pump_report_sections(
            chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
            infusion_pump_checks, flow_acc, occ_press,
            min_flow, max_flow, min_press, max_press,
            flow_unit, press_unit, bubble_ad_water, bubble_ad_dry,
        )
    if device_category == "シリンジポンプ":
        return build_syringe_pump_report_sections(
            chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
            flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
            flow_unit, press_unit,
        )
    if device_category == "保育器" and is_incu_i_incubator(device_category, device_model):
        return build_incu_i_report_sections(
            incu_i_checks or default_incu_i_checks(),
            incu_i_measurements or default_incu_i_measurements(),
        )
    if device_category == "保育器":
        return {
            "form": "incubator",
            "sections": [{
                "title": "点検項目",
                "kind": "check",
                "items": [_check_item(k, v) for k, v in inc_o_checks.items()],
            }],
        }
    if device_category == "生体情報モニタ":
        return build_vsm_report_sections(
            vsm_checks or default_vsm_checks(),
            vsm_measurements or default_vsm_measurements(),
            vsm_meta or default_vsm_meta(),
        )
    if device_category == "心電計":
        return build_ecg_report_sections(
            ecg_checks or default_ecg_checks(),
            ecg_measurements or default_ecg_measurements(),
        )
    return None

def flatten_report_sections(report_sections):
    """セクション構成をフラットな (項目, 結果, 判定) 行に変換"""
    if not report_sections or not report_sections.get("sections"):
        return []
    rows = []
    for section in report_sections["sections"]:
        for item in section.get("items", []):
            if item.get("sub_items"):
                for sub in item["sub_items"]:
                    rows.append((
                        f"{item.get('name', '')}({sub.get('name', '')})",
                        sub.get("result", ""),
                        sub.get("judge", ""),
                    ))
            elif "standard" in item:
                rows.append((item.get("name", ""), item.get("result", ""), item.get("judge", "")))
            else:
                rows.append((item.get("name", ""), item.get("result", ""), item.get("judge", "")))
    return rows

def serialize_inspection_detail(detail_text, item_rows=None, check_type="", report_sections=None):
    """点検項目をスプレッドシート向けに永続化（セクション構成 JSON）"""
    readable = str(detail_text or "").strip()
    if report_sections:
        flat_items = flatten_report_sections(report_sections) or (item_rows or [])
        payload = {
            "v": INSPECTION_DETAIL_JSON_VERSION,
            "form": report_sections.get("form", ""),
            "check_type": clean_data_str(check_type),
            "text": readable,
            "sections": report_sections.get("sections", []),
            "items": [[str(a), str(b), str(c)] for a, b, c in flat_items],
        }
        return json.dumps(payload, ensure_ascii=False)
    if item_rows:
        payload = {
            "v": 1,
            "check_type": clean_data_str(check_type),
            "text": readable,
            "items": [[str(a), str(b), str(c)] for a, b, c in item_rows],
        }
        return json.dumps(payload, ensure_ascii=False)
    return readable

def parse_stored_inspection_detail(raw):
    """保存済み詳細データを (可読テキスト, 項目行, 作業区分, セクション構成) に復元"""
    raw_s = "" if raw is None or (isinstance(raw, float) and pd.isna(raw)) else str(raw).strip()
    if not raw_s or raw_s.lower() == "nan":
        return "", None, "", None
    if raw_s.startswith("{"):
        try:
            data = json.loads(raw_s)
            if isinstance(data, dict):
                check_type = clean_data_str(data.get("check_type", ""))
                text = clean_data_str(data.get("text", "")) or raw_s
                report_sections = None
                if data.get("sections"):
                    report_sections = {
                        "form": data.get("form", ""),
                        "sections": data.get("sections", []),
                    }
                    items = flatten_report_sections(report_sections) or None
                    return text, items, check_type, report_sections
                if data.get("items"):
                    items = [
                        (str(x[0]), str(x[1]), str(x[2]))
                        for x in data["items"]
                        if isinstance(x, (list, tuple)) and len(x) >= 3
                    ]
                    return text, items or None, check_type, None
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return raw_s, None, "", None

def get_history_detail_raw(row):
    """点検履歴行から詳細データの生文字列を取得"""
    if row is None:
        return ""
    for col in ("詳細データ", "詳細", "点検詳細"):
        val = row.get(col, "")
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        val_s = str(val).strip()
        if val_s and val_s.lower() != "nan":
            return val_s
    return ""

def get_history_detail_text(row):
    """点検履歴行から可読な詳細テキストを取得"""
    raw = get_history_detail_raw(row)
    text, _, _, _ = parse_stored_inspection_detail(raw)
    return text or raw

def resolve_history_inspection(row):
    """点検履歴行から印刷用 (詳細テキスト, 項目行, 作業区分, セクション構成) を取得"""
    raw = get_history_detail_raw(row)
    detail_text, item_rows, check_type, report_sections = parse_stored_inspection_detail(raw)
    if not detail_text:
        detail_text = raw
    return detail_text, item_rows, check_type, report_sections

def resolve_inspection_table_rows(detail_text="", item_rows=None):
    """報告書用の点検項目行を解決（構造化データ優先、なければ詳細データを解析）"""
    if item_rows:
        names, results, judges = [], [], []
        for row in item_rows:
            if not row or len(row) < 3:
                continue
            names.append(str(row[0]))
            results.append(str(row[1]))
            judges.append(str(row[2]))
        if names:
            return names, results, judges
    return parse_detail_text_to_table(detail_text)

def classify_inspection_type(detail_text="", memo="", result=""):
    """点検履歴行を定期点検 / 修理・故障対応に分類"""
    detail = str(detail_text or "")
    memo_s = str(memo or "")
    result_s = str(result or "")
    if "【故障修理後点検】" in detail or "故障対応" in result_s or "元故障症状" in memo_s:
        return "修理・故障対応"
    return "定期点検"

def inspection_report_title(report_kind="定期点検"):
    if report_kind == "修理・故障対応":
        return "医療機器 修理・点検完了報告書"
    return "医療機器定期点検報告書"

def filter_history_for_device(df_history, target_me, master_row=None):
    """点検履歴を機器で絞り込み（現管理番号・旧番号・シリアルNo に対応）"""
    if df_history is None or df_history.empty:
        return pd.DataFrame()
    tokens = _device_match_tokens(target_me, master_row)
    if not tokens:
        return pd.DataFrame()

    working = normalize_inspection_history_df(df_history)
    normalized_me = clean_series(working["管理番号"])
    mask = normalized_me.isin(tokens)

    master_sn = clean_data_str(master_row.get("シリアルNo", "")) if master_row is not None else ""
    if master_sn:
        hist_sn = clean_series(working["シリアルNo"])
        mask = mask | (hist_sn == master_sn)

    result = working[mask]
    if result.empty:
        return pd.DataFrame()
    return result.iloc[::-1]

def filter_fault_reports_for_device(df_fault, target_me, master_row=None):
    """故障報告を機器で絞り込み（現管理番号・旧番号に対応）"""
    if df_fault is None or df_fault.empty or "管理番号" not in df_fault.columns:
        return pd.DataFrame()
    tokens = _device_match_tokens(target_me, master_row)
    if not tokens:
        return pd.DataFrame()
    mask = clean_series(df_fault["管理番号"]).isin(tokens)
    result = df_fault[mask]
    if result.empty:
        return pd.DataFrame()
    return result.iloc[::-1]

def _history_record_label(row, list_index=0):
    detail_for_label = get_history_detail_text(row)
    kind = classify_inspection_type(
        detail_for_label, row.get("備考", ""), row.get("判定", ""),
    )
    return (
        f"{clean_data_str(row.get('点検日', ''))} | {kind} | "
        f"{clean_data_str(row.get('管理番号', ''))} | "
        f"{clean_data_str(row.get('機種', ''))} | "
        f"{clean_data_str(row.get('判定', ''))} [#{list_index + 1}]"
    )

def _section_item_reference(item):
    parts = []
    if item.get("note"):
        parts.append(str(item["note"]))
    if item.get("standard"):
        parts.append(f"基準値：{item['standard']}")
    return " / ".join(parts)

def _render_inspection_sections_screen(report_sections):
    """入力フォームと同じセクション構成で点検結果を表示"""
    for section in report_sections.get("sections", []):
        st.markdown(f"**{section.get('title', '点検項目')}**")
        if section.get("kind") in ("measure", "mixed"):
            rows = []
            for item in section.get("items", []):
                if item.get("sub_items"):
                    rows.append({
                        "点検・測定項目": item.get("name", ""),
                        "基準・備考": _section_item_reference(item),
                        "実測値": "",
                        "判定": "",
                    })
                    for sub in item["sub_items"]:
                        rows.append({
                            "点検・測定項目": f"　{sub.get('name', '')}",
                            "基準・備考": sub.get("standard", ""),
                            "実測値": sub.get("result", ""),
                            "判定": sub.get("judge", ""),
                        })
                elif "standard" in item:
                    rows.append({
                        "点検・測定項目": item.get("name", ""),
                        "基準・備考": _section_item_reference(item),
                        "実測値": item.get("result", ""),
                        "判定": item.get("judge", ""),
                    })
                else:
                    rows.append({
                        "点検・測定項目": item.get("name", ""),
                        "基準・備考": item.get("note", ""),
                        "実測値": item.get("result", ""),
                        "判定": item.get("judge", ""),
                    })
            if rows:
                st.table(pd.DataFrame(rows))
        else:
            rows = [{
                "点検項目": item.get("name", ""),
                "結果": item.get("result", ""),
                "判定": item.get("judge", ""),
            } for item in section.get("items", [])]
            if rows:
                st.table(pd.DataFrame(rows))

def _append_inspection_sections_pdf(story, report_sections, font_name):
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle, Spacer

    for section in report_sections.get("sections", []):
        story.append(_daily_monthly_pdf_paragraph(section.get("title", "点検項目"), font_name, 10))
        story.append(Spacer(1, 1.5 * mm))
        if section.get("kind") in ("measure", "mixed"):
            table_data = [[
                _daily_monthly_pdf_paragraph("点検・測定項目", font_name, 8),
                _daily_monthly_pdf_paragraph("基準・備考", font_name, 8),
                _daily_monthly_pdf_paragraph("実測値", font_name, 8),
                _daily_monthly_pdf_paragraph("判定", font_name, 8),
            ]]
            judge_rows = []
            for item in section.get("items", []):
                if item.get("sub_items"):
                    table_data.append([
                        _daily_monthly_pdf_paragraph(item.get("name", ""), font_name, 8),
                        _daily_monthly_pdf_paragraph(_section_item_reference(item), font_name, 7),
                        _daily_monthly_pdf_paragraph("", font_name, 8),
                        _daily_monthly_pdf_paragraph("", font_name, 8),
                    ])
                    judge_rows.append("")
                    for sub in item["sub_items"]:
                        table_data.append([
                            _daily_monthly_pdf_paragraph(f"  {sub.get('name', '')}", font_name, 8),
                            _daily_monthly_pdf_paragraph(sub.get("standard", ""), font_name, 7),
                            _daily_monthly_pdf_paragraph(sub.get("result", ""), font_name, 8),
                            _daily_monthly_pdf_paragraph(sub.get("judge", ""), font_name, 8),
                        ])
                        judge_rows.append(sub.get("judge", ""))
                elif "standard" in item:
                    table_data.append([
                        _daily_monthly_pdf_paragraph(item.get("name", ""), font_name, 8),
                        _daily_monthly_pdf_paragraph(_section_item_reference(item), font_name, 7),
                        _daily_monthly_pdf_paragraph(item.get("result", ""), font_name, 8),
                        _daily_monthly_pdf_paragraph(item.get("judge", ""), font_name, 8),
                    ])
                    judge_rows.append(item.get("judge", ""))
                else:
                    table_data.append([
                        _daily_monthly_pdf_paragraph(item.get("name", ""), font_name, 8),
                        _daily_monthly_pdf_paragraph(item.get("note", ""), font_name, 7),
                        _daily_monthly_pdf_paragraph(item.get("result", ""), font_name, 8),
                        _daily_monthly_pdf_paragraph(item.get("judge", ""), font_name, 8),
                    ])
                    judge_rows.append(item.get("judge", ""))
            col_widths = [40 * mm, 62 * mm, 28 * mm, 18 * mm]
        else:
            table_data = [[
                _daily_monthly_pdf_paragraph("点検項目", font_name, 8),
                _daily_monthly_pdf_paragraph("結果", font_name, 8),
                _daily_monthly_pdf_paragraph("判定", font_name, 8),
            ]]
            judge_rows = []
            for item in section.get("items", []):
                table_data.append([
                    _daily_monthly_pdf_paragraph(item.get("name", ""), font_name, 8),
                    _daily_monthly_pdf_paragraph(item.get("result", ""), font_name, 8),
                    _daily_monthly_pdf_paragraph(item.get("judge", ""), font_name, 8),
                ])
                judge_rows.append(item.get("judge", ""))
            col_widths = [78 * mm, 48 * mm, 22 * mm]

        detail_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
        judge_col = 3 if section.get("kind") in ("measure", "mixed") else 2
        for row_idx, judge in enumerate(judge_rows, start=1):
            sym = normalize_check_symbol(judge)
            if sym == "×":
                style_cmds.append(("BACKGROUND", (judge_col, row_idx), (judge_col, row_idx), colors.HexColor("#ffcdd2")))
            elif sym == "〇":
                style_cmds.append(("BACKGROUND", (judge_col, row_idx), (judge_col, row_idx), colors.HexColor("#c8e6c9")))
            elif sym == "△":
                style_cmds.append(("BACKGROUND", (judge_col, row_idx), (judge_col, row_idx), colors.HexColor("#fff9c4")))
        detail_table.setStyle(TableStyle(style_cmds))
        story.extend([detail_table, Spacer(1, 3 * mm)])

def render_inspection_report(check_date, me_no, model_name, inspector, result, detail_text="", memo="",
                             device_category="", report_kind="定期点検", unique_key_suffix="",
                             item_rows=None, check_type_label="", report_sections=None):
    st.markdown("""
    <style>
    @media print {
        header, [data-testid="stSidebar"], footer, .no-print { display: none !important; }
        .block-container { max-width: 100% !important; padding-top: 0 !important; }
    }
    </style>
    """, unsafe_allow_html=True)

    pdf_bytes = build_inspection_report_pdf_bytes(
        check_date, me_no, model_name, inspector, result, detail_text, memo, device_category,
        report_kind=report_kind, item_rows=item_rows, check_type_label=check_type_label,
        report_sections=report_sections,
    )
    pdf_name = f"点検報告_{clean_data_str(me_no)}_{check_date}.pdf"
    pdf_key = f"inspection_pdf_{clean_data_str(me_no)}_{check_date}_{unique_key_suffix or report_kind}"
    col_pdf, col_hint = st.columns([1, 2])
    with col_pdf:
        st.download_button(
            "PDFをダウンロード",
            data=pdf_bytes,
            file_name=pdf_name,
            mime="application/pdf",
            type="primary",
            key=pdf_key,
        )
    with col_hint:
        st.caption("A4縦向きPDF。ブラウザの「印刷」→「PDFに保存」（Cmd/Ctrl + P）でも保存できます。")

    title = report_sections.get("title") if report_sections else None
    title = title or inspection_report_title(report_kind)
    st.write(f"## {title} （{check_date} 実施分）")
    info_rows = {
        "管理番号": me_no,
        "機種(型式)": model_name,
        "点検実施者": inspector,
        "総合評価": result,
    }
    if device_category:
        info_rows["機器種類"] = device_category
    if check_type_label:
        info_rows["作業区分"] = check_type_label
    info_rows["点検区分"] = report_kind
    info_df = pd.DataFrame({"項目": list(info_rows.keys()), "内容": list(info_rows.values())})
    st.table(info_df.set_index("項目"))

    if report_sections and report_sections.get("sections"):
        _render_inspection_sections_screen(report_sections)
    else:
        item_names, item_results, item_judges = resolve_inspection_table_rows(detail_text, item_rows)
        if item_names:
            excel_df = pd.DataFrame({
                "点検・測定項目": item_names,
                "点検実測値 / 結果": item_results,
                "判定": item_judges,
            })
            st.table(excel_df)
        elif detail_text and str(detail_text).strip().lower() not in ("", "nan"):
            st.markdown("**点検・測定結果**")
            st.text(str(detail_text))

    if memo and str(memo).strip().lower() not in ("", "nan"):
        st.info(f"備考・処置内容:\n{memo}")

def build_inspection_report_pdf_bytes(check_date, me_no, model_name, inspector, result,
                                      detail_text="", memo="", device_category="",
                                      report_kind="定期点検", item_rows=None, check_type_label="",
                                      report_sections=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer

    font_name = _daily_monthly_pdf_font()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )
    story = [
        _daily_monthly_pdf_paragraph(
            (report_sections.get("title") if report_sections else None)
            or inspection_report_title(report_kind),
            font_name, 14, align=1,
        ),
        _daily_monthly_pdf_paragraph(f"作業日: {check_date}", font_name, 10, align=1),
        Spacer(1, 4 * mm),
    ]

    header_rows = [
        ["管理番号", clean_data_str(me_no), "機種(型式)", clean_data_str(model_name)],
        ["点検実施者", clean_data_str(inspector), "総合評価", clean_data_str(result)],
    ]
    if device_category:
        header_rows.append(["機器種類", clean_data_str(device_category), "点検区分", report_kind])
    else:
        header_rows.append(["点検区分", report_kind, "", ""])
    if check_type_label:
        header_rows.append(["作業区分", check_type_label, "", ""])

    header_table_data = []
    for row in header_rows:
        header_table_data.append([
            _daily_monthly_pdf_paragraph(cell, font_name, 9) for cell in row
        ])
    header_table = Table(header_table_data, colWidths=[32 * mm, 52 * mm, 32 * mm, 52 * mm])
    header_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e8e8e8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#e8e8e8")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.extend([header_table, Spacer(1, 5 * mm)])

    if report_sections and report_sections.get("sections"):
        _append_inspection_sections_pdf(story, report_sections, font_name)
    else:
        item_names, item_results, item_judges = resolve_inspection_table_rows(detail_text, item_rows)
        if item_names:
            story.append(_daily_monthly_pdf_paragraph("点検・測定結果", font_name, 11))
            story.append(Spacer(1, 2 * mm))
            detail_table_data = [[
                _daily_monthly_pdf_paragraph("点検・測定項目", font_name, 8),
                _daily_monthly_pdf_paragraph("点検実測値 / 結果", font_name, 8),
                _daily_monthly_pdf_paragraph("判定", font_name, 8),
            ]]
            for name, res, judge in zip(item_names, item_results, item_judges):
                detail_table_data.append([
                    _daily_monthly_pdf_paragraph(name, font_name, 8),
                    _daily_monthly_pdf_paragraph(res, font_name, 8),
                    _daily_monthly_pdf_paragraph(judge, font_name, 8),
                ])
            detail_table = Table(
                detail_table_data,
                colWidths=[62 * mm, 58 * mm, 28 * mm],
                repeatRows=1,
            )
            style_cmds = [
                ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
            for row_idx, judge in enumerate(item_judges, start=1):
                sym = normalize_check_symbol(judge)
                if sym == "×":
                    style_cmds.append(("BACKGROUND", (2, row_idx), (2, row_idx), colors.HexColor("#ffcdd2")))
                elif sym == "〇":
                    style_cmds.append(("BACKGROUND", (2, row_idx), (2, row_idx), colors.HexColor("#c8e6c9")))
                elif sym == "△":
                    style_cmds.append(("BACKGROUND", (2, row_idx), (2, row_idx), colors.HexColor("#fff9c4")))
            detail_table.setStyle(TableStyle(style_cmds))
            story.extend([detail_table, Spacer(1, 4 * mm)])

    if memo and str(memo).strip().lower() not in ("", "nan"):
        story.extend([
            _daily_monthly_pdf_paragraph("備考・処置内容", font_name, 10),
            _daily_monthly_pdf_paragraph(memo, font_name, 9),
            Spacer(1, 3 * mm),
        ])

    story.append(_daily_monthly_pdf_paragraph(
        f"出力日時: {format_jst(fmt='%Y-%m-%d %H:%M')}　|　miratech 医療機器管理システム",
        font_name, 7,
    ))
    doc.build(story)
    return buf.getvalue()

def render_inspection_history_viewer(conn, df_master, df_history):
    """点検表履歴の一覧表示・報告書印刷（定期点検 / 修理・故障対応）"""
    st.markdown("#### 点検表履歴・印刷")
    st.caption("定期点検および修理・故障対応点検の報告書を、過去の履歴から表示・PDF保存できます。保存以降の点検は項目データも履歴に残り、後日印刷できます。")

    if st.button("履歴データを最新にする", key="hist_view_refresh"):
        st.cache_data.clear()
        st.rerun()

    col_search, col_filter = st.columns([2, 1])
    with col_search:
        keyword = st.text_input(
            "管理番号・旧番号 または シリアルNo（空欄ですべて表示）",
            placeholder="例: INP0001",
            key="hist_view_search",
        ).strip()
    with col_filter:
        type_filter = st.selectbox(
            "点検区分で絞り込み",
            ["すべて", "定期点検", "修理・故障対応"],
            key="hist_view_type",
        )

    working = df_history.copy() if df_history is not None and not df_history.empty else pd.DataFrame()

    if keyword:
        if df_master.empty:
            st.warning("機器マスターが読み込めません。")
            return
        master_row, match_type = find_device_row(df_master, keyword)
        if master_row is None:
            st.warning("該当する機器が見つかりません。管理番号・旧番号・シリアルNo を確認してください。")
            return
        target_me = clean_data_str(master_row.get("管理番号", ""))
        if match_type == "旧番号":
            st.info(f"旧番号「{clean_data_str(keyword)}」→ 管理番号 {target_me} の履歴を表示します。")
        working = filter_history_for_device(working, target_me, master_row)

    if working.empty:
        st.info("表示できる点検履歴がありません。")
        return

    working = working.copy()
    working["_点検区分"] = working.apply(
        lambda r: classify_inspection_type(
            get_history_detail_text(r), r.get("備考", ""), r.get("判定", ""),
        ),
        axis=1,
    )
    if type_filter != "すべて":
        working = working[working["_点検区分"] == type_filter]

    if working.empty:
        st.info(f"「{type_filter}」に該当する履歴はありません。")
        return

    st.write(f"**{len(working)} 件** の点検表が見つかりました。")
    display_cols = [c for c in ["点検日", "管理番号", "カテゴリ", "機種", "_点検区分", "実施者", "判定"] if c in working.columns]
    list_df = working[display_cols].rename(columns={"_点検区分": "点検区分"})
    st.dataframe(_sanitize_dataframe(list_df), use_container_width=True, hide_index=True)

    label_map = {}
    for list_index, (row_idx, row) in enumerate(working.iterrows()):
        label_map[_history_record_label(row, list_index)] = row_idx

    selected_label = st.selectbox(
        "印刷・表示する点検表を選択",
        list(label_map.keys()),
        key="hist_view_record_select",
    )
    report_row = working.loc[label_map[selected_label]]
    report_me = clean_data_str(report_row.get("管理番号", ""))
    report_model = normalize_stored_model(
        report_row.get("カテゴリ", ""),
        report_row.get("機種", ""),
    ) or clean_data_str(report_row.get("機種", ""))
    report_detail, report_items, report_check_type, report_sections = resolve_history_inspection(report_row)
    report_kind = classify_inspection_type(
        report_detail,
        report_row.get("備考", ""),
        report_row.get("判定", ""),
    )

    st.markdown("---")
    st.subheader("点検報告書プレビュー")
    render_inspection_report(
        report_row.get("点検日", ""),
        report_me,
        report_model,
        report_row.get("実施者", "-"),
        report_row.get("判定", "-"),
        report_detail,
        report_row.get("備考", ""),
        device_category=clean_data_str(report_row.get("カテゴリ", "")),
        report_kind=report_kind,
        unique_key_suffix=str(label_map[selected_label]),
        item_rows=report_items,
        check_type_label=report_check_type,
        report_sections=report_sections,
    )

def ensure_inspection_history_worksheet():
    """点検履歴シートとヘッダー列を確保（詳細データ列の欠落を防止）"""
    client, spreadsheet_id = _get_sheet_client()
    sh = client.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet("点検履歴")
        header = [h for h in ws.row_values(1) if h]
        missing = [c for c in INSPECTION_HISTORY_COLUMNS if c not in header]
        if missing:
            ws.update([header + missing], "A1")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="点検履歴", rows=2000, cols=len(INSPECTION_HISTORY_COLUMNS))
        ws.update([INSPECTION_HISTORY_COLUMNS], "A1")
    st.cache_data.clear()

def save_inspection_to_sheets(conn, final_me_no, final_sn, device_category, device_model,
                              scan_year_val, check_date, check_type, inspector, result,
                              memo, detail_text, item_rows=None, report_sections=None):
    """点検結果を機器マスター・点検履歴シートへ保存する"""
    df_master = safe_read_worksheet(conn, "機器マスター", ["管理番号", "最終点検日", "最終判定", "最終実施者"])
    if df_master.empty or "管理番号" not in df_master.columns:
        raise ValueError("機器マスターの読み込みに失敗しました。通信環境を確認してください。")

    for col in ["最終点検日", "最終判定", "最終実施者"]:
        if col not in df_master.columns:
            df_master[col] = ""

    mask = clean_series(df_master["管理番号"]) == clean_data_str(final_me_no)
    if not mask.any():
        raise ValueError(f"マスターに管理番号「{final_me_no}」が見つかりません。")

    df_master.loc[mask, "最終点検日"] = str(check_date)
    df_master.loc[mask, "最終判定"] = f"{result}({check_type})"
    df_master.loc[mask, "最終実施者"] = inspector
    conn.update(worksheet="機器マスター", data=df_master)

    existing_history = safe_read_worksheet(conn, "点検履歴", INSPECTION_HISTORY_COLUMNS)
    stored_detail = serialize_inspection_detail(
        detail_text, item_rows, check_type=check_type, report_sections=report_sections,
    )

    new_hist_row = {
        "点検日": str(check_date),
        "管理番号": final_me_no,
        "カテゴリ": device_category,
        "シリアルNo": final_sn,
        "製造年月日": scan_year_val,
        "機種": model_for_spreadsheet(device_model),
        "実施者": inspector,
        "判定": result,
        "詳細データ": stored_detail,
        "備考": memo,
    }
    updated_history = append_inspection_history_row(existing_history, new_hist_row)
    ensure_inspection_history_worksheet()
    conn.update(worksheet="点検履歴", data=_sanitize_dataframe(updated_history))

FAULT_REPORT_COLUMNS = [
    "報告日", "発生日", "管理番号", "機種", "報告者", "部署", "症状", "対応状況",
]

def is_fault_pending(status_val):
    status = clean_data_str(status_val).lower()
    return status in ("", "未対応", "nan", "none")

def _fault_report_label(row):
    me = clean_data_str(row.get("管理番号", "不明"))
    model = clean_data_str(row.get("機種", "不明"))
    dept = clean_data_str(row.get("部署", ""))
    symptom = clean_data_str(row.get("症状", ""))
    rep_date = clean_data_str(row.get("報告日", ""))
    return f"{me} - {model} ({dept} / 症状: {symptom}) 報告日: {rep_date}"

def save_repair_completion(conn, selected_idx, repair_date, repair_detail,
                           chk_r1, chk_r2, chk_r3, repair_result, repair_memo, inspector):
    """故障報告の対応完了・点検履歴・機器マスターを更新"""
    st.cache_data.clear()
    df_failed = safe_read_worksheet(conn, "故障報告", FAULT_REPORT_COLUMNS, raise_on_fail=True)
    if "対応状況" not in df_failed.columns:
        df_failed["対応状況"] = "未対応"

    if selected_idx not in df_failed.index:
        raise ValueError("選択した故障報告が見つかりません。画面を更新して再度お試しください。")

    current_status = clean_data_str(df_failed.at[selected_idx, "対応状況"])
    if not is_fault_pending(current_status):
        raise ValueError(f"この故障報告は既に対応済みです（{current_status}）")

    job_data = df_failed.loc[selected_idx]
    target_me = clean_data_str(job_data.get("管理番号", ""))
    if not target_me:
        raise ValueError("管理番号が空の故障報告は処理できません。")

    df_failed.at[selected_idx, "対応状況"] = f"対応済 ({repair_date})"
    conn.update(worksheet="故障報告", data=_sanitize_dataframe(df_failed))

    chk_str = f"外観:{'〇' if chk_r1 else '×'}, 作動:{'〇' if chk_r2 else '×'}, 警報:{'〇' if chk_r3 else '×'}"
    readable_detail = f"【故障修理後点検】 処置: {repair_detail} / 安全確認: {chk_str}"
    repair_item_rows = [
        ("修理・処置内容", clean_data_str(repair_detail), "-"),
        ("外観点検", "〇" if chk_r1 else "×", "〇" if chk_r1 else "×"),
        ("作動点検", "〇" if chk_r2 else "×", "〇" if chk_r2 else "×"),
        ("警報点検", "〇" if chk_r3 else "×", "〇" if chk_r3 else "×"),
    ]
    detail_text = serialize_inspection_detail(
        readable_detail,
        repair_item_rows,
        check_type="修理・故障対応",
    )

    df_m_lookup = safe_read_worksheet(
        conn, "機器マスター", ["管理番号", "カテゴリ", "製造年月日", "最終点検日", "最終判定", "最終実施者"],
        raise_on_fail=True,
    )
    device_category = "その他"
    scan_year_val = ""
    serial_no = ""
    m_row = df_m_lookup[clean_series(df_m_lookup["管理番号"]) == target_me]
    if not m_row.empty:
        master_rec = m_row.iloc[0]
        device_category = clean_data_str(master_rec.get("カテゴリ", "その他"))
        scan_year_val = clean_data_str(master_rec.get("製造年月日", ""))
        serial_no = clean_data_str(master_rec.get("シリアルNo", ""))

    existing_history = safe_read_worksheet(conn, "点検履歴", INSPECTION_HISTORY_COLUMNS, raise_on_fail=True)

    new_hist_row = {
        "点検日": str(repair_date),
        "管理番号": target_me,
        "カテゴリ": device_category,
        "シリアルNo": serial_no,
        "製造年月日": scan_year_val,
        "機種": clean_data_str(job_data.get("機種", "")),
        "実施者": inspector,
        "判定": repair_result,
        "詳細データ": detail_text,
        "備考": f"元故障症状: {clean_data_str(job_data.get('症状', ''))} / 備考: {repair_memo}",
    }
    updated_history = append_inspection_history_row(existing_history, new_hist_row)
    ensure_inspection_history_worksheet()
    conn.update(worksheet="点検履歴", data=_sanitize_dataframe(updated_history))

    if not df_m_lookup.empty:
        for col in ["最終点検日", "最終判定", "最終実施者"]:
            if col not in df_m_lookup.columns:
                df_m_lookup[col] = ""
        mask_m = clean_series(df_m_lookup["管理番号"]) == target_me
        if mask_m.any():
            df_m_lookup.loc[mask_m, "最終点検日"] = str(repair_date)
            df_m_lookup.loc[mask_m, "最終判定"] = f"{repair_result}(故障対応)"
            df_m_lookup.loc[mask_m, "最終実施者"] = inspector
            conn.update(worksheet="機器マスター", data=_sanitize_dataframe(df_m_lookup))

    write_log(inspector, f"{target_me} の故障対応・修理点検を完了")
    return target_me, job_data, detail_text

def _build_repair_report_html(target_me, job_data, repair_date, repair_detail,
                              chk_r1, chk_r2, chk_r3, repair_result, inspector):
    return f"""
    <div style="padding: 30px; border: 2px solid #333; background-color: white; color: black; border-radius: 5px; font-family: sans-serif;">
        <h2 style="text-align: center; border-bottom: 2px solid black; padding-bottom: 10px; margin-top:0;">医療機器 修理・点検完了報告書</h2>
        <div style="display: flex; justify-content: space-between; margin-bottom: 20px;">
            <div><b>提出先:</b> 現場責任者 / 看護師長 殿</div>
            <div><b>完了報告日:</b> {html.escape(str(repair_date))}</div>
        </div>
        <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 20px;">
            <tr>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>管理番号</b></td>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%;">{html.escape(str(target_me))}</td>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>対象機種</b></td>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%;">{html.escape(clean_data_str(job_data.get('機種', '')))}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>故障発生部署</b></td>
                <td style="padding: 10px; border: 1px solid #aaa;">{html.escape(clean_data_str(job_data.get('部署', '')))}</td>
                <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>初期報告者</b></td>
                <td style="padding: 10px; border: 1px solid #aaa;">{html.escape(clean_data_str(job_data.get('報告者', '')))}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>現場報告の症状</b></td>
                <td colspan="3" style="padding: 10px; border: 1px solid #aaa;">{html.escape(clean_data_str(job_data.get('症状', '')))}</td>
            </tr>
        </table>
        <h4 style="border-left: 4px solid #333; padding-left: 8px; margin-bottom: 10px;">■ 修理・処置内容</h4>
        <div style="padding: 10px; border: 1px solid #aaa; min-height: 50px; margin-bottom: 20px; background-color: #fafafa;">
            {html.escape(str(repair_detail)).replace(chr(10), '<br/>')}
        </div>
        <h4 style="border-left: 4px solid #333; padding-left: 8px; margin-bottom: 10px;">■ 出荷前・現場安全点検結果 (翌日実施分含む)</h4>
        <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 20px; text-align: center;">
            <tr style="background-color: #f0f0f0;">
                <th style="padding: 8px; border: 1px solid #aaa;">点検項目</th>
                <th style="padding: 8px; border: 1px solid #aaa;">判定</th>
                <th style="padding: 8px; border: 1px solid #aaa;">点検項目</th>
                <th style="padding: 8px; border: 1px solid #aaa;">判定</th>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #aaa; text-align: left;">1. 外観・筐体破損チェック</td>
                <td style="padding: 8px; border: 1px solid #aaa; color: green; font-weight: bold;">{'正常 (適合)' if chk_r1 else '不適合'}</td>
                <td style="padding: 8px; border: 1px solid #aaa; text-align: left;">3. 各種警報・アラーム作動確認</td>
                <td style="padding: 8px; border: 1px solid #aaa; color: green; font-weight: bold;">{'正常 (適合)' if chk_r3 else '不適合'}</td>
            </tr>
            <tr>
                <td style="padding: 8px; border: 1px solid #aaa; text-align: left;">2. 通電・実作動シーケンスチェック</td>
                <td style="padding: 8px; border: 1px solid #aaa; color: green; font-weight: bold;">{'正常 (適合)' if chk_r2 else '不適合'}</td>
                <td style="padding: 8px; border: 1px solid #aaa; text-align: left;">4. その他総合安全性</td>
                <td style="padding: 8px; border: 1px solid #aaa; color: green; font-weight: bold;">適合</td>
            </tr>
        </table>
        <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-top: 20px;">
            <tr>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>総合判定</b></td>
                <td style="padding: 10px; border: 1px solid #aaa; font-size: 16px; color: red; font-weight: bold;">{html.escape(str(repair_result))}</td>
                <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>点検技術者（実施者）</b></td>
                <td style="padding: 10px; border: 1px solid #aaa; text-align: center;">{html.escape(str(inspector))} (印)</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>施設側 収領・確認印</b></td>
                <td colspan="3" style="padding: 25px; border: 1px solid #aaa; text-align: right; color: #ccc;">確認日: &nbsp;&nbsp;&nbsp;&nbsp;年 &nbsp;&nbsp;&nbsp;月 &nbsp;&nbsp;&nbsp;日 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; サイン / 職印欄: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>
            </tr>
        </table>
        <p style="text-align: right; font-size: 11px; color: gray; margin-top: 15px; margin-bottom: 0;">技術管理・保守責任: miratech 琉球 医療機器管理システム</p>
    </div>
    """

def render_repair_fault_management(conn):
    """修理・故障対応管理（未対応故障報告の処理・報告書）"""
    st.subheader("修理故障・対応管理")
    st.caption("現場からの故障報告に対する修理対応・安全点検の記録")

    if st.session_state.get("repair_saved_report"):
        report = st.session_state["repair_saved_report"]
        st.success(f"{report['target_me']} の修理対応・安全点検の記録を保存し、台帳を更新しました！")
        st.markdown("---")
        st.subheader("提出用 報告書の印刷レイアウト")
        st.markdown(report["html"], unsafe_allow_html=True)
        st.info("Cmd/Ctrl + P で印刷またはPDF保存できます。")
        if st.button("次の対応入力をする", type="primary", key="repair_done_refresh"):
            st.session_state.pop("repair_saved_report", None)
            st.cache_data.clear()
            st.rerun()
        return

    try:
        df_failed = safe_read_worksheet(conn, "故障報告", FAULT_REPORT_COLUMNS)

        col_refresh, col_view = st.columns([1, 2])
        with col_refresh:
            if st.button("故障報告データを更新", key="repair_refresh_fault"):
                st.cache_data.clear()
                st.rerun()
        with col_view:
            st.caption("故障報告シートの一覧は下段で確認できます。")

        if df_failed.empty:
            st.info("現在、故障報告データはありません。")
        else:
            if "対応状況" not in df_failed.columns:
                df_failed["対応状況"] = "未対応"

            pending_mask = df_failed["対応状況"].apply(is_fault_pending)
            df_pending = df_failed[pending_mask]

            st.markdown("#### 故障報告一覧")
            display_dataframe(df_failed.iloc[::-1], hide_index=True, use_container_width=True)

            st.markdown("---")
            st.markdown("#### 故障対応・修理完了の入力")

            if df_pending.empty:
                st.success("現在、対応待ちの故障報告はありません。すべての修理・点検が完了しています！")
            else:
                st.warning(f"現在、**{len(df_pending)} 件** の未対応の故障報告があります。")

                pending_labels = {}
                for row_idx, row in df_pending.iterrows():
                    pending_labels[_fault_report_label(row)] = row_idx

                selected_job = st.selectbox(
                    "対応する故障報告を選択してください",
                    list(pending_labels.keys()),
                    key="repair_job_select",
                )
                selected_idx = pending_labels[selected_job]
                target_me = clean_data_str(df_failed.loc[selected_idx].get("管理番号", ""))

                with st.form("repair_form"):
                    st.info(f"対象機器: {target_me} の修理対応・点検結果を入力します。")
                    repair_date = st.date_input("対応完了日（現場点検日）", value=date.today())
                    repair_detail = st.text_area(
                        "修理・処置内容",
                        placeholder="例: 包包交換、内部清掃、設定リセット実施",
                    )
                    st.write("修理後の安全点検チェック（エビデンス確保）")
                    chk_r1 = st.checkbox("外観点検（汚れ、破損、変形がないこと）", value=True)
                    chk_r2 = st.checkbox("作動点検（基本動作、セルフチェックが正常なこと）", value=True)
                    chk_r3 = st.checkbox("警報点検（アラーム、シミュレータテスト正常なこと）", value=True)
                    repair_result = st.radio(
                        "総合評価", ["使用可", "メーカー修理依頼", "廃棄手続き"], horizontal=True,
                    )
                    repair_memo = st.text_area("備考（特記事項があれば）")

                    if st.form_submit_button("修理・点検完了を確定する", type="primary"):
                        inspector = st.session_state.get("current_user_name", "ME")
                        try:
                            saved_me, job_data, _detail = save_repair_completion(
                                conn,
                                selected_idx,
                                repair_date,
                                repair_detail,
                                chk_r1,
                                chk_r2,
                                chk_r3,
                                repair_result,
                                repair_memo,
                                inspector,
                            )
                            st.session_state["repair_saved_report"] = {
                                "target_me": saved_me,
                                "html": _build_repair_report_html(
                                    saved_me, job_data, repair_date, repair_detail,
                                    chk_r1, chk_r2, chk_r3, repair_result, inspector,
                                ),
                            }
                            st.cache_data.clear()
                            st.rerun()
                        except SheetReadError as e:
                            st.error(f"スプレッドシート読み込みエラー: {e}")
                        except Exception as e:
                            st.error(f"保存に失敗しました: {e}")

    except Exception as e:
        st.error(f"故障データの処理中にエラーが発生しました: {e}")

def _validate_check_dict(checks_dict, ng_items, incomplete_items):
    for label, val in checks_dict.items():
        if is_check_incomplete(val):
            incomplete_items.append(label)
        elif is_check_ng(val):
            ng_items.append(label)

def _validate_incu_i_measurements(measurements, ng_items):
    m = measurements
    ranges = [
        ("体重モニタ(中央)", m["体重モニタ(中央)"], 4995, 5005, "g"),
        ("体重モニタ(四隅)", m["体重モニタ(四隅)"], 4990, 5010, "g"),
        ("湿度コントロール(表示)", m["湿度コントロール(表示)"], 87, 93, "%"),
        ("湿度コントロール(実測)", m["湿度コントロール(実測)"], 80, 100, "%"),
        ("マニュアルコントロール(表示)", m["マニュアルコントロール(表示)"], 33, 35, "℃"),
        ("マニュアルコントロール(実測)", m["マニュアルコントロール(実測)"], 33, 35, "℃"),
        ("サーボコントロール(表示)", m["サーボコントロール(表示)"], 33.5, 34.5, "℃"),
        ("サーボコントロール(実測)", m["サーボコントロール(実測)"], 33.5, 34.5, "℃"),
        ("酸素濃度制御(表示)", m["酸素濃度制御(表示)"], 37, 43, "%"),
        ("酸素濃度制御(実測)", m["酸素濃度制御(実測)"], 37, 43, "%"),
    ]
    for name, val, lo, hi, unit in ranges:
        if not (lo <= val <= hi):
            ng_items.append(f"{name}（{val}{unit}）")
    if m["酸素接続口1(実測)"] < 65:
        ng_items.append(f"酸素接続口1（{m['酸素接続口1(実測)']}%）")
    limits = [
        ("接地漏れ電流(正常)", m["接地漏れ電流(正常)"], 200),
        ("接地漏れ電流(単一故障)", m["接地漏れ電流(単一故障)"], 500),
        ("外装漏れ電流(正常)", m["外装漏れ電流(正常)"], 100),
        ("外装漏れ電流(単一故障)", m["外装漏れ電流(単一故障)"], 500),
        ("患者漏れ電流I(正常)", m["患者漏れ電流I(正常)"], 100),
        ("患者漏れ電流I(単一故障)", m["患者漏れ電流I(単一故障)"], 500),
    ]
    for name, val, limit in limits:
        if leakage_ua_int(val) > limit:
            ng_items.append(f"{name}（{leakage_ua_int(val)}μA）")

def validate_inspection_items(device_category, check_type, result, inc_o_checks,
                              chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                              flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                              flow_unit="ml", press_unit="kPa",
                              infusion_pump_checks=None,
                              bubble_ad_water=0.0, bubble_ad_dry=0.0,
                              device_model="",
                              incu_i_checks=None,
                              incu_i_measurements=None,
                              vsm_checks=None,
                              vsm_measurements=None,
                              vsm_meta=None,
                              ecg_checks=None,
                              ecg_measurements=None):
    """点検項目のNG・未入力を検出する。戻り値: (ng_items, incomplete_items)"""
    ng_items = []
    incomplete_items = []
    infusion_pump_checks = infusion_pump_checks or {}

    if check_type != "院内点検(miratech)":
        return ng_items, incomplete_items

    if device_category in ["輸液ポンプ", "シリンジポンプ"]:
        appearance_vals = [chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7]
        pump_checks = dict(zip(INFUSION_PUMP_APPEARANCE_ITEMS, appearance_vals))
        _validate_check_dict(pump_checks, ng_items, incomplete_items)

        if device_category == "輸液ポンプ":
            _validate_check_dict(infusion_pump_checks, ng_items, incomplete_items)

        if result == "使用可":
            if not (min_flow <= flow_acc <= max_flow):
                ng_items.append(f"流量精度（{flow_acc} {flow_unit}）")
            if not (min_press <= occ_press <= max_press):
                ng_items.append(f"閉塞検出（{occ_press} {press_unit}）")
            if device_category == "輸液ポンプ":
                if bubble_ad_water < 100:
                    ng_items.append(f"気泡センサーAD値(水入り)（{bubble_ad_water}）")
                if bubble_ad_dry > 10:
                    ng_items.append(f"気泡センサーAD値(水無し)（{bubble_ad_dry}）")

    elif device_category == "保育器":
        if is_incu_i_incubator(device_category, device_model):
            _validate_check_dict(
                incu_i_checks or default_incu_i_checks(), ng_items, incomplete_items,
            )
            if result == "使用可":
                _validate_incu_i_measurements(
                    incu_i_measurements or default_incu_i_measurements(), ng_items,
                )
        else:
            _validate_check_dict(inc_o_checks, ng_items, incomplete_items)

    elif device_category == "生体情報モニタ":
        _validate_check_dict(
            vsm_checks or default_vsm_checks(), ng_items, incomplete_items,
        )
        if result == "使用可":
            _validate_leakage_currents(
                vsm_measurements or default_vsm_measurements(), ng_items,
            )

    elif device_category == "心電計":
        _validate_check_dict(
            ecg_checks or default_ecg_checks(), ng_items, incomplete_items,
        )
        if result == "使用可":
            _validate_ecg_measurements(
                ecg_measurements or default_ecg_measurements(), ng_items,
            )

    return ng_items, incomplete_items

def is_unselected(val):
    """後方互換。未選択のみ判定（---=機能なしは選択済み扱い）"""
    return is_check_incomplete(val)

def build_inspection_item_rows(check_type, device_category, result, inc_o_checks,
                               chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                               flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                               flow_unit, press_unit,
                               infusion_pump_checks=None,
                               bubble_ad_water=0.0, bubble_ad_dry=0.0):
    """点検報告書用の項目行 [(項目名, 結果, 判定), ...] を生成"""
    rows = []
    infusion_pump_checks = infusion_pump_checks or {}

    if check_type == "院内点検(miratech)":
        if device_category in ["輸液ポンプ", "シリンジポンプ"]:
            appearance_vals = [chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7]
            for label, val in zip(INFUSION_PUMP_APPEARANCE_ITEMS, appearance_vals):
                val_s = normalize_check_symbol(val) or "---"
                rows.append((label, val_s, val_s))
            if device_category == "輸液ポンプ":
                for label in INFUSION_PUMP_ALARM_ITEMS + INFUSION_PUMP_FUNCTION_ITEMS:
                    val_s = normalize_check_symbol(infusion_pump_checks.get(label, "---")) or "---"
                    rows.append((label, val_s, val_s))
            flow_judge = measure_judge(min_flow <= flow_acc <= max_flow)
            press_judge = measure_judge(min_press <= occ_press <= max_press)
            rows.append(("流量精度", f"{flow_acc} {flow_unit}", flow_judge))
            rows.append(("閉塞検出", f"{occ_press} {press_unit}", press_judge))
            if device_category == "輸液ポンプ":
                water_judge = measure_judge(bubble_ad_water >= 100)
                dry_judge = measure_judge(bubble_ad_dry <= 10)
                rows.append(("気泡センサーAD値(水入り)", str(bubble_ad_water), water_judge))
                rows.append(("気泡センサーAD値(水無し)", str(bubble_ad_dry), dry_judge))
        elif device_category == "保育器":
            for label, val in inc_o_checks.items():
                val_s = normalize_check_symbol(val) or "---"
                rows.append((label, val_s, val_s))

    return rows

def build_inspection_detail_text(check_type, device_category, result, inc_o_checks,
                                 chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                 flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                                 flow_unit, press_unit,
                                 infusion_pump_checks=None,
                                 bubble_ad_water=0.0, bubble_ad_dry=0.0):
    rows = build_inspection_item_rows(
        check_type, device_category, result, inc_o_checks,
        chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
        flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
        flow_unit, press_unit,
        infusion_pump_checks=infusion_pump_checks,
        bubble_ad_water=bubble_ad_water,
        bubble_ad_dry=bubble_ad_dry,
    )
    parts_list = []
    for name, result_val, judge in rows:
        if judge in ("〇", "×", "△", "OK", "NG") and normalize_check_symbol(result_val) != normalize_check_symbol(judge):
            parts_list.append(f"{name}:{result_val} ({judge})")
        else:
            parts_list.append(f"{name}:{result_val}")

    if check_type == "院内点検(miratech)" and device_category in ["輸液ポンプ", "シリンジポンプ"]:
        parts_list.extend([
            f"基準流量:{min_flow}～{max_flow}",
            f"基準閉塞:{min_press}～{max_press} {press_unit}",
        ])

    detail_text = " | ".join(parts_list)
    if check_type != "院内点検(miratech)":
        detail_text = f"点検区分:{check_type}" + (f" | {detail_text}" if detail_text else "")
    return detail_text

def build_inspection_save_bundle(check_type, device_category, result, inc_o_checks,
                                 chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                 flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                                 flow_unit, press_unit,
                                 infusion_pump_checks=None,
                                 bubble_ad_water=0.0, bubble_ad_dry=0.0,
                                 device_model="",
                                 incu_i_checks=None,
                                 incu_i_measurements=None,
                                 vsm_checks=None,
                                 vsm_measurements=None,
                                 vsm_meta=None,
                                 ecg_checks=None,
                                 ecg_measurements=None):
    """保存・印刷用に詳細データ・項目行・セクション構成をまとめて生成"""
    report_sections = build_inspection_report_sections(
        check_type, device_category, inc_o_checks,
        chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
        flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
        flow_unit, press_unit,
        infusion_pump_checks=infusion_pump_checks,
        bubble_ad_water=bubble_ad_water,
        bubble_ad_dry=bubble_ad_dry,
        device_model=device_model,
        incu_i_checks=incu_i_checks,
        incu_i_measurements=incu_i_measurements,
        vsm_checks=vsm_checks,
        vsm_measurements=vsm_measurements,
        vsm_meta=vsm_meta,
        ecg_checks=ecg_checks,
        ecg_measurements=ecg_measurements,
    )
    item_rows = flatten_report_sections(report_sections) if report_sections else build_inspection_item_rows(
        check_type, device_category, result, inc_o_checks,
        chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
        flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
        flow_unit, press_unit,
        infusion_pump_checks=infusion_pump_checks,
        bubble_ad_water=bubble_ad_water,
        bubble_ad_dry=bubble_ad_dry,
    )
    if check_type != "院内点検(miratech)" and not item_rows:
        item_rows = [("点検区分", check_type, check_type)]
    detail_text = build_inspection_detail_text(
        check_type, device_category, result, inc_o_checks,
        chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
        flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
        flow_unit, press_unit,
        infusion_pump_checks=infusion_pump_checks,
        bubble_ad_water=bubble_ad_water,
        bubble_ad_dry=bubble_ad_dry,
    )
    return detail_text, item_rows, report_sections

def execute_inspection_save(conn, final_me_no, final_sn, device_category, device_model,
                            scan_year_val, check_date, check_type, inspector, result,
                            memo, detail_text, item_rows=None, report_sections=None):
    save_inspection_to_sheets(
        conn, final_me_no, final_sn, device_category, device_model,
        scan_year_val, check_date, check_type, inspector, result,
        memo, detail_text, item_rows=item_rows, report_sections=report_sections,
    )
    write_log(inspector, f"{final_me_no} の点検を登録")
    st.session_state["last_check_date"] = check_date
    st.session_state["check_registered_msg"] = f"{final_me_no} の点検データを登録しました。"
    return {
        "check_date": check_date,
        "final_me_no": final_me_no,
        "model_name": model_for_spreadsheet(device_model),
        "device_category": device_category,
        "inspector": inspector,
        "result": result,
        "detail_text": detail_text,
        "memo": memo,
        "check_type": check_type,
        "item_rows": item_rows or [],
        "report_sections": report_sections,
        "report_kind": "定期点検",
    }

# ==========================================
# 日常点検（動作点検）— 超音波診断装置・保育器
# ==========================================
DAILY_INSPECTION_CATEGORIES = {"超音波診断装置", "保育器"}

DAILY_CHECK_ITEMS = {
    "超音波診断装置": [
        "電源投入・起動正常",
        "表示画面・タッチパネル正常",
        "プローブ接続・認識正常",
        "画像表示正常",
        "冷却ファン作動・異音なし",
        "外装・コード類異常なし",
    ],
    "保育器": [
        "表示・設定温度確認",
        "温度警報作動確認",
        "ヒータ作動確認",
        "キャノピ開閉動作",
        "外装・清潔状態",
        "電源コード・接続部",
    ],
}

DAILY_HISTORY_COLUMNS = [
    "点検日", "管理番号", "カテゴリ", "シリアルNo", "機種",
    "実施者", "総合判定", "詳細データ", "備考",
]

def ensure_daily_history_worksheet():
    """日常点検履歴シートが無ければ自動作成"""
    client, spreadsheet_id = _get_sheet_client()
    sh = client.open_by_key(spreadsheet_id)
    try:
        sh.worksheet("日常点検履歴")
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title="日常点検履歴", rows=1000, cols=len(DAILY_HISTORY_COLUMNS))
        ws.update([DAILY_HISTORY_COLUMNS], "A1")
    st.cache_data.clear()

def validate_daily_checks(checks):
    ng_items = []
    incomplete_items = []
    for label, val in checks.items():
        if is_check_incomplete(val):
            incomplete_items.append(label)
        elif is_check_ng(val):
            ng_items.append(label)
    return ng_items, incomplete_items

def build_daily_detail_text(checks):
    return " | ".join(f"{k}:{v}" for k, v in checks.items())

def save_daily_inspection_to_sheets(conn, final_me_no, final_sn, device_category, device_model,
                                    check_date, inspector, overall_result, memo, detail_text):
    ensure_daily_history_worksheet()
    existing = safe_read_worksheet(conn, "日常点検履歴", DAILY_HISTORY_COLUMNS)
    if existing.empty:
        existing = pd.DataFrame(columns=DAILY_HISTORY_COLUMNS)

    new_row = {
        "点検日": str(check_date),
        "管理番号": protect_zeros(final_me_no),
        "カテゴリ": device_category,
        "シリアルNo": protect_zeros(final_sn),
        "機種": model_for_spreadsheet(device_model),
        "実施者": inspector,
        "総合判定": overall_result,
        "詳細データ": detail_text,
        "備考": memo,
    }
    for col in existing.columns:
        if col not in new_row:
            new_row[col] = ""

    updated = pd.concat([existing, pd.DataFrame([new_row])[existing.columns]], ignore_index=True)
    conn.update(worksheet="日常点検履歴", data=updated)

def execute_daily_inspection_save(conn, final_me_no, final_sn, device_category, device_model,
                                  check_date, inspector, overall_result, memo, detail_text,
                                  msg_key="daily_check_registered_msg"):
    save_daily_inspection_to_sheets(
        conn, final_me_no, final_sn, device_category, device_model,
        check_date, inspector, overall_result, memo, detail_text,
    )
    write_log(inspector, f"{final_me_no} の日常点検を登録 ({overall_result})")
    st.session_state["last_daily_check_date"] = check_date
    st.session_state[msg_key] = f"{final_me_no} の日常点検を登録しました。（{overall_result}）"
    return {
        "check_date": check_date,
        "final_me_no": final_me_no,
        "model_name": model_for_spreadsheet(device_model),
        "device_category": device_category,
        "inspector": inspector,
        "overall_result": overall_result,
        "detail_text": detail_text,
        "memo": memo,
    }

def render_daily_inspection_report(check_date, me_no, device_category, model_name, inspector,
                                   overall_result, detail_text="", memo=""):
    st.write(f"## 日常点検（動作点検）記録 （{check_date} 実施分）")
    info_df = pd.DataFrame({
        "管理番号": [me_no],
        "機器種類": [device_category],
        "型式": [model_name],
        "実施者": [inspector],
        "総合判定": [overall_result],
    })
    st.table(info_df)

    item_names, item_results, item_judges = parse_detail_text_to_table(detail_text)
    if item_names:
        st.table(pd.DataFrame({
            "点検項目": item_names,
            "判定": item_judges,
        }))

    if memo and str(memo).strip().lower() not in ("", "nan"):
        st.info(f"備考:\n{memo}")

def render_daily_inspection_form(conn, df_master, initial_keyword="", form_key_prefix="daily",
                                 locked_keyword=False):
    msg_key = f"{form_key_prefix}_registered_msg"
    pending_key = f"{form_key_prefix}_pending_save"
    search_key = f"{form_key_prefix}_last_search_keyword"

    if st.session_state.get(msg_key):
        st.success(st.session_state[msg_key])

    if locked_keyword and initial_keyword:
        st.success(f"対象機器: {initial_keyword}")
        input_keyword = clean_data_str(initial_keyword)
    else:
        input_keyword = st.text_input(
            "管理番号・旧番号 または シリアルNo を入力して検索",
            value=initial_keyword,
            placeholder="例: US0001 または 旧番号",
            key=f"{form_key_prefix}_search_keyword",
            disabled=locked_keyword,
        ).strip()

    if input_keyword != st.session_state.get(search_key, ""):
        st.session_state.pop(msg_key, None)
        st.session_state.pop(pending_key, None)
        st.session_state[search_key] = input_keyword

    master_row = None
    match_type = None
    if input_keyword and not df_master.empty:
        master_row, match_type = find_device_row(df_master, input_keyword)

    if master_row is None:
        if input_keyword:
            st.warning("該当する機器が見つかりません。管理番号・旧番号・シリアルNo を確認してください。")
        else:
            st.info("日常点検は「超音波診断装置」と「保育器」のみ対象です。管理番号等を入力して検索してください。")
        return

    final_me_no = clean_data_str(master_row.get("管理番号", ""))
    final_sn = clean_data_str(master_row.get("シリアルNo", ""))
    device_category = clean_data_str(master_row.get("カテゴリ", "その他"))
    device_model = normalize_stored_model(device_category, master_row.get("機種", ""))

    if match_type == "旧番号":
        st.info(
            f"旧番号「{clean_data_str(input_keyword)}」で見つかりました。"
            f" 現在の管理番号は {final_me_no} です。"
        )
    elif not locked_keyword:
        st.success("登録済みの機器が見つかりました。")

    sync_device_display_fields(form_key_prefix, final_me_no, device_category, final_sn, device_model)

    if device_category not in DAILY_INSPECTION_CATEGORIES:
        st.error(
            f"「{device_category}」は日常点検の対象外です。"
            f" 対象: {', '.join(sorted(DAILY_INSPECTION_CATEGORIES))}"
        )
        return

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        st.text_input("管理番号", value=final_me_no, disabled=True, key=f"{form_key_prefix}_disp_me")
        st.text_input("機器の種類", value=device_category, disabled=True, key=f"{form_key_prefix}_disp_cat")
    with col_m2:
        st.text_input("シリアルNo", value=final_sn, disabled=True, key=f"{form_key_prefix}_disp_sn")
        st.text_input("型式", value=device_model, disabled=True, key=f"{form_key_prefix}_disp_model")

    check_labels = DAILY_CHECK_ITEMS[device_category]
    daily_checks = {label: "---" for label in check_labels}

    st.markdown("---")
    st.write("**日常点検項目**")
    st.caption(INSPECTION_CHECK_LEGEND)

    if "last_daily_check_date" not in st.session_state:
        st.session_state["last_daily_check_date"] = date.today()

    saved_report = None
    with st.form(f"{form_key_prefix}_check_form"):
        check_date = st.date_input("点検日", value=st.session_state["last_daily_check_date"])
        inspector = st.text_input("実施者", value=st.session_state.get("current_user_name", ""))

        cols = st.columns(2)
        for idx, label in enumerate(check_labels):
            with cols[idx % 2]:
                daily_checks[label] = st.radio(
                    label, INSPECTION_CHECK_OPTIONS, horizontal=True, index=None, key=f"{form_key_prefix}_chk_{idx}",
                )

        memo = st.text_area("備考", placeholder="特記事項があれば記入してください")
        submitted = st.form_submit_button("日常点検を保存", type="primary", use_container_width=True)

    if submitted:
        if not inspector.strip():
            st.warning("実施者を入力してください。")
        else:
            ng_items, incomplete_items = validate_daily_checks(daily_checks)
            detail_text = build_daily_detail_text(daily_checks)
            overall_result = "日常NG" if ng_items else "日常OK"
            save_payload = {
                "final_me_no": final_me_no,
                "final_sn": final_sn,
                "device_category": device_category,
                "device_model": device_model,
                "check_date": check_date,
                "inspector": inspector,
                "overall_result": overall_result,
                "memo": memo,
                "detail_text": detail_text,
                "incomplete_items": incomplete_items,
                "ng_items": ng_items,
                "msg_key": msg_key,
            }

            if incomplete_items:
                st.error(f"未選択の項目があります。{INSPECTION_CHECK_LEGEND.replace('判定: ', '')} のいずれかを選択してください。")
                st.warning("未設定: " + "、".join(incomplete_items))
                st.session_state[pending_key] = save_payload
            else:
                if ng_items:
                    st.warning("問題項目: " + "、".join(ng_items))
                st.session_state.pop(pending_key, None)
                try:
                    with st.spinner("保存しています..."):
                        saved_report = execute_daily_inspection_save(
                            conn,
                            **{k: v for k, v in save_payload.items()
                               if k not in ("incomplete_items", "ng_items", "msg_key")},
                            msg_key=msg_key,
                        )
                    st.success(st.session_state[msg_key])
                except Exception as e:
                    st.error(f"保存エラー: {e}")

    pending = st.session_state.get(pending_key)
    if pending:
        st.markdown("---")
        st.warning("未選択の項目があります。このまま保存しますか？")
        st.write("未設定: " + "、".join(pending.get("incomplete_items", [])))
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("Yes（保存する）", type="primary", use_container_width=True,
                         key=f"{form_key_prefix}_confirm_yes"):
                try:
                    with st.spinner("保存しています..."):
                        saved_report = execute_daily_inspection_save(
                            conn,
                            **{k: v for k, v in pending.items()
                               if k not in ("incomplete_items", "ng_items", "msg_key")},
                            msg_key=pending.get("msg_key", msg_key),
                        )
                    st.session_state.pop(pending_key, None)
                    st.success(st.session_state[pending.get("msg_key", msg_key)])
                except Exception as e:
                    st.error(f"保存エラー: {e}")
        with col_no:
            if st.button("No（キャンセル）", use_container_width=True, key=f"{form_key_prefix}_confirm_no"):
                st.session_state.pop(pending_key, None)
                st.info("保存をキャンセルしました。")
                st.rerun()

    if saved_report:
        render_daily_inspection_report(
            saved_report["check_date"],
            saved_report["final_me_no"],
            saved_report["device_category"],
            saved_report["model_name"],
            saved_report["inspector"],
            saved_report["overall_result"],
            saved_report["detail_text"],
            saved_report["memo"],
        )

def parse_daily_detail_to_dict(detail_text):
    result = {}
    if not detail_text or str(detail_text).strip().lower() in ("", "nan"):
        return result
    for part in str(detail_text).split("|"):
        part = part.strip()
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        result[key.strip()] = val.strip()
    return result

def parse_check_date_flexible(date_str):
    s = clean_data_str(date_str)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    matched = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if matched:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3)))
    return None

def list_daily_inspection_devices(df_master):
    if df_master is None or df_master.empty or "カテゴリ" not in df_master.columns:
        return []
    devices = []
    for _, row in df_master.iterrows():
        category = clean_data_str(row.get("カテゴリ", ""))
        if category not in DAILY_INSPECTION_CATEGORIES:
            continue
        me_no = clean_data_str(row.get("管理番号", ""))
        if not me_no:
            continue
        model = normalize_stored_model(category, row.get("機種", ""))
        label = f"{me_no} | {category} | {model or '型式未登録'}"
        devices.append({
            "me_no": me_no,
            "label": label,
            "category": category,
            "model": model,
            "serial_no": clean_data_str(row.get("シリアルNo", "")),
            "location": clean_data_str(row.get("設置場所", "")),
        })
    return sorted(devices, key=lambda d: d["me_no"])

def load_daily_history_for_device_month(conn, me_no, year, month):
    df = safe_read_worksheet(conn, "日常点検履歴", DAILY_HISTORY_COLUMNS)
    if df.empty or "管理番号" not in df.columns:
        return pd.DataFrame(columns=DAILY_HISTORY_COLUMNS)

    clean_me = clean_data_str(me_no)
    filtered = df[clean_series(df["管理番号"]) == clean_me].copy()
    if filtered.empty:
        return pd.DataFrame(columns=DAILY_HISTORY_COLUMNS)

    rows = []
    for _, row in filtered.iterrows():
        check_day = parse_check_date_flexible(row.get("点検日", ""))
        if check_day and check_day.year == year and check_day.month == month:
            rows.append(row)
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=DAILY_HISTORY_COLUMNS)

def build_monthly_daily_table_rows(device_category, month_df, year, month):
    check_labels = DAILY_CHECK_ITEMS.get(device_category, [])
    num_days = calendar.monthrange(year, month)[1]
    by_day = {}
    for _, row in month_df.iterrows():
        check_day = parse_check_date_flexible(row.get("点検日", ""))
        if check_day:
            by_day[check_day.day] = row

    header = ["点検項目"] + [str(day) for day in range(1, num_days + 1)]
    rows = [header]

    for label in check_labels:
        row = [label]
        for day in range(1, num_days + 1):
            if day in by_day:
                detail = parse_daily_detail_to_dict(by_day[day].get("詳細データ", ""))
                val = detail.get(label, "")
                sym = normalize_check_symbol(val)
                row.append(sym if sym in ("〇", "△", "×", "---") else "")
            else:
                row.append("")
        rows.append(row)

    overall_row = ["総合判定"]
    inspector_row = ["実施者"]
    for day in range(1, num_days + 1):
        if day in by_day:
            overall_row.append(clean_data_str(by_day[day].get("総合判定", "")))
            inspector_row.append(clean_data_str(by_day[day].get("実施者", "")))
        else:
            overall_row.append("")
            inspector_row.append("")
    rows.extend([overall_row, inspector_row])
    return rows, num_days, len(by_day)

def _daily_monthly_pdf_font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    font_name = "HeiseiKakuGo-W5"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    except Exception:
        font_name = "Helvetica"
    return font_name

def _daily_monthly_pdf_paragraph(text, font_name, font_size=7, align=0):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    style = ParagraphStyle(
        name=f"daily_pdf_{font_size}_{align}",
        fontName=font_name,
        fontSize=font_size,
        leading=font_size + 2,
        alignment=align,
    )
    safe_text = html.escape(str(text or "")).replace("\n", "<br/>")
    return Paragraph(safe_text, style)

def _build_monthly_pdf_story(facility_name, device_info, year, month, table_rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle, Spacer

    font_name = _daily_monthly_pdf_font()
    page_size = landscape(A4)
    me_no = device_info["me_no"]
    category = device_info["category"]
    model = device_info.get("model", "")
    serial_no = device_info.get("serial_no", "")
    location = device_info.get("location", "")

    story = [
        _daily_monthly_pdf_paragraph(
            f"医療機器 日常点検記録表（動作点検） — {year}年{month}月",
            font_name, 12, align=1,
        ),
        Spacer(1, 4 * mm),
        _daily_monthly_pdf_paragraph(
            f"{facility_name}　|　管理番号: {me_no}　|　{category}　|　型式: {model or '-'}　|　"
            f"シリアルNo: {serial_no or '-'}　|　設置場所: {location or '-'}",
            font_name, 8,
        ),
        Spacer(1, 4 * mm),
    ]

    num_cols = len(table_rows[0])
    usable_width = page_size[0] - 16 * mm
    label_width = 42 * mm
    day_width = max(5.5 * mm, (usable_width - label_width) / max(num_cols - 1, 1))
    col_widths = [label_width] + [day_width] * (num_cols - 1)

    pdf_table_data = []
    for row_idx, row in enumerate(table_rows):
        pdf_row = []
        for col_idx, cell in enumerate(row):
            size = 6 if col_idx > 0 else 7
            align = 1 if row_idx == 0 or col_idx > 0 else 0
            pdf_row.append(_daily_monthly_pdf_paragraph(cell, font_name, size, align=align))
        pdf_table_data.append(pdf_row)

    table = Table(pdf_table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f5f5f5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]
    for row_idx, row in enumerate(table_rows):
        if row_idx == 0:
            continue
        for col_idx, cell in enumerate(row):
            if col_idx == 0:
                continue
            style_cls = check_cell_style_class(cell)
            if style_cls == "ng":
                style_cmds.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), colors.HexColor("#ffcdd2")))
            elif style_cls == "ok":
                style_cmds.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), colors.HexColor("#c8e6c9")))
            elif style_cls == "warn":
                style_cmds.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), colors.HexColor("#fff9c4")))
            elif style_cls == "na":
                style_cmds.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), colors.HexColor("#eeeeee")))
    table.setStyle(TableStyle(style_cmds))

    story.extend([
        table,
        Spacer(1, 3 * mm),
        _daily_monthly_pdf_paragraph(
            "※ 空欄は未実施。"
            f" {INSPECTION_CHECK_LEGEND.replace('判定: ', '')}。"
            f" 印刷日: "
            f"{format_jst(fmt='%Y-%m-%d %H:%M')}　|　PDF出力: miratech 日常点検管理",
            font_name, 7,
        ),
    ])
    return story

def build_monthly_daily_inspection_pdf_bytes(facility_name, device_info, year, month, table_rows):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    doc.build(_build_monthly_pdf_story(facility_name, device_info, year, month, table_rows))
    buf.seek(0)
    return buf.getvalue()

def build_monthly_daily_inspection_pdf_for_devices(facility_name, devices_data, year, month):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, SimpleDocTemplate

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=8 * mm,
        rightMargin=8 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
    )
    story = []
    for idx, item in enumerate(devices_data):
        if idx > 0:
            story.append(PageBreak())
        story.extend(_build_monthly_pdf_story(
            facility_name, item["device_info"], year, month, item["table_rows"],
        ))
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()

def build_monthly_daily_inspection_html(facility_name, device_info, year, month, table_rows, record_count):
    me_no = html.escape(device_info["me_no"])
    category = html.escape(device_info["category"])
    model = html.escape(device_info.get("model", "") or "-")
    serial_no = html.escape(device_info.get("serial_no", "") or "-")
    location = html.escape(device_info.get("location", "") or "-")

    header_cells = "".join(
        f'<th>{html.escape(str(cell))}</th>' for cell in table_rows[0]
    )
    body_rows = []
    for row in table_rows[1:]:
        cells = []
        for col_idx, cell in enumerate(row):
            cls = "label-col" if col_idx == 0 else "day-col"
            val = html.escape(str(cell))
            style_cls = check_cell_style_class(cell)
            if col_idx > 0 and style_cls == "ng":
                cls += " ng"
            elif col_idx > 0 and style_cls == "ok":
                cls += " ok"
            elif col_idx > 0 and style_cls == "warn":
                cls += " warn"
            elif col_idx > 0 and style_cls == "na":
                cls += " na"
            cells.append(f'<td class="{cls}">{val}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return f"""
    <div class="daily-monthly-report">
        <style>
            @page {{ size: A4 landscape; margin: 10mm; }}
            .daily-monthly-report {{
                font-family: "Hiragino Sans", "Yu Gothic", "Meiryo", sans-serif;
                color: #111;
                background: #fff;
                padding: 8px;
            }}
            .daily-monthly-report h2 {{
                text-align: center;
                margin: 0 0 8px 0;
                font-size: 18px;
                border-bottom: 2px solid #333;
                padding-bottom: 6px;
            }}
            .daily-monthly-report .meta {{
                font-size: 12px;
                margin-bottom: 10px;
                line-height: 1.5;
            }}
            .daily-monthly-report table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
                font-size: 10px;
            }}
            .daily-monthly-report th, .daily-monthly-report td {{
                border: 1px solid #666;
                padding: 3px 2px;
                text-align: center;
                word-break: break-all;
            }}
            .daily-monthly-report th {{
                background: #ececec;
            }}
            .daily-monthly-report .label-col {{
                text-align: left;
                width: 120px;
                background: #f7f7f7;
                font-size: 9px;
            }}
            .daily-monthly-report .day-col.ok {{ background: #e8f5e9; font-weight: bold; }}
            .daily-monthly-report .day-col.ng {{ background: #ffcdd2; font-weight: bold; color: #b71c1c; }}
            .daily-monthly-report .day-col.warn {{ background: #fff9c4; font-weight: bold; }}
            .daily-monthly-report .day-col.na {{ background: #eeeeee; color: #666; }}
            .daily-monthly-report .footnote {{
                margin-top: 8px;
                font-size: 10px;
                color: #444;
            }}
            @media print {{
                header, [data-testid="stSidebar"], footer, .no-print {{ display: none !important; }}
                .daily-monthly-report {{ padding: 0; }}
            }}
        </style>
        <h2>医療機器 日常点検記録表（動作点検） — {year}年{month}月</h2>
        <div class="meta">
            <b>{html.escape(facility_name)}</b><br>
            管理番号: {me_no}　|　{category}　|　型式: {model}　|　シリアルNo: {serial_no}　|　設置場所: {location}<br>
            当月記録件数: {record_count} 件
        </div>
        <table>
            <thead><tr>{header_cells}</tr></thead>
            <tbody>{"".join(body_rows)}</tbody>
        </table>
        <div class="footnote">
            ※ 空欄は未実施。ブラウザの「印刷」→「PDFに保存」でA4横向きPDF化できます（Cmd/Ctrl + P）。
        </div>
    </div>
    """

def render_monthly_daily_inspection_export(conn, df_master, facility_name):
    st.markdown("#### 月次日常点検記録表（A4 PDF・印刷）")
    st.caption("指定した年月の日常点検結果を、日付×点検項目の1ヶ月表として出力できます。")

    devices = list_daily_inspection_devices(df_master)
    if not devices:
        st.info("日常点検対象の機器（超音波診断装置・保育器）がマスターに登録されていません。")
        return

    today = date.today()
    col_y, col_m, col_mode = st.columns([1, 1, 1.2])
    with col_y:
        target_year = st.number_input("年", min_value=2020, max_value=2100, value=today.year, key="monthly_daily_year")
    with col_m:
        target_month = st.selectbox("月", list(range(1, 13)), index=today.month - 1, key="monthly_daily_month")
    with col_mode:
        export_mode = st.radio(
            "出力対象",
            ["1台ずつ", "対象機器すべて（1 PDF）"],
            horizontal=True,
            key="monthly_daily_export_mode",
        )

    if export_mode == "1台ずつ":
        device_labels = [d["label"] for d in devices]
        selected_label = st.selectbox("対象機器", device_labels, key="monthly_daily_device")
        selected_devices = [d for d in devices if d["label"] == selected_label]
    else:
        selected_devices = devices
        st.info(f"対象機器 {len(selected_devices)} 台分を1つのPDFにまとめます（機器ごとに1ページ）。")

    if st.button("月次記録表を生成", type="primary", key="monthly_daily_generate"):
        devices_data = []
        preview_items = []
        for device in selected_devices:
            month_df = load_daily_history_for_device_month(conn, device["me_no"], target_year, target_month)
            table_rows, _, record_count = build_monthly_daily_table_rows(
                device["category"], month_df, target_year, target_month,
            )
            device_info = {
                "me_no": device["me_no"],
                "category": device["category"],
                "model": device["model"],
                "serial_no": device["serial_no"],
                "location": device["location"],
            }
            devices_data.append({
                "device_info": device_info,
                "table_rows": table_rows,
                "record_count": record_count,
            })
            preview_items.append({
                "device_info": device_info,
                "table_rows": table_rows,
                "record_count": record_count,
                "html": build_monthly_daily_inspection_html(
                    facility_name, device_info, target_year, target_month, table_rows, record_count,
                ),
            })

        if len(devices_data) == 1:
            pdf_bytes = build_monthly_daily_inspection_pdf_bytes(
                facility_name, devices_data[0]["device_info"], target_year, target_month, devices_data[0]["table_rows"],
            )
            pdf_name = f"日常点検_{devices_data[0]['device_info']['me_no']}_{target_year}{target_month:02d}.pdf"
        else:
            pdf_bytes = build_monthly_daily_inspection_pdf_for_devices(
                facility_name, devices_data, target_year, target_month,
            )
            pdf_name = f"日常点検_全機器_{target_year}{target_month:02d}.pdf"

        st.session_state["monthly_daily_pdf_bytes"] = pdf_bytes
        st.session_state["monthly_daily_pdf_name"] = pdf_name
        st.session_state["monthly_daily_preview_items"] = preview_items

    if st.session_state.get("monthly_daily_pdf_bytes"):
        st.download_button(
            "PDFをダウンロード",
            data=st.session_state["monthly_daily_pdf_bytes"],
            file_name=st.session_state.get("monthly_daily_pdf_name", "daily_inspection.pdf"),
            mime="application/pdf",
            type="primary",
            key="monthly_daily_pdf_download",
        )
        st.caption("ダウンロードしたPDFはA4横向きです。印刷設定も「横向き」を選んでください。")

    preview_items = st.session_state.get("monthly_daily_preview_items", [])
    if preview_items:
        st.markdown("---")
        st.markdown("##### 印刷プレビュー")
        st.info("下のプレビュー表示中に Cmd/Ctrl + P でもPDF保存できます。")
        for item in preview_items:
            st.markdown(item["html"], unsafe_allow_html=True)
            if len(preview_items) > 1:
                st.markdown("---")

# --- ログ書き込み用共通関数 ---
def write_log(user_name, action):
    try:
        conn = get_sheet_conn()
        df_logs = safe_read_worksheet(conn, "アクセスログ", ["日時", "ユーザー名", "アクション"])
        
        new_log = pd.DataFrame([{
            "日時": format_jst(),
            "ユーザー名": user_name,
            "アクション": action
        }])
        updated_logs = pd.concat([df_logs, new_log], ignore_index=True)
        conn.update(worksheet="アクセスログ", data=updated_logs)
    except Exception:
        pass 

# ==========================================
# ログインセッション（Cookie で保持）
# ==========================================
def _auth_serializer():
    secret = st.secrets.get(
        "AUTH_SECRET",
        st.secrets.get("GEMINI_API_KEY", "miratech-session-secret"),
    )
    return URLSafeTimedSerializer(str(secret), salt="miratech-auth")

def save_auth_cookie(user_id, user_name):
    token = _auth_serializer().dumps({
        "uid": user_id,
        "name": user_name,
        "facility": "miratech 琉球 管理センター",
    })
    get_cookie_manager().set(
        AUTH_COOKIE_NAME,
        token,
        expires_at=now_jst() + timedelta(days=SESSION_MAX_AGE_DAYS),
        key="save_auth_cookie",
    )
    touch_activity()

def clear_auth_cookie():
    cm = get_cookie_manager()
    cm.delete(AUTH_COOKIE_NAME, key="clear_auth_cookie")
    cm.delete(LAST_ACTIVE_COOKIE, key="clear_last_active_cookie")
    st.session_state.pop("last_activity", None)

def _get_last_activity():
    return st.session_state.get("last_activity")

def touch_activity():
    now = time.time()
    st.session_state["last_activity"] = now
    get_cookie_manager().set(
        LAST_ACTIVE_COOKIE,
        str(int(now)),
        expires_at=now_jst() + timedelta(days=SESSION_MAX_AGE_DAYS),
        key="touch_last_active",
    )

def enforce_idle_timeout():
    last = _get_last_activity()
    if last is not None and time.time() - last > IDLE_SECONDS:
        logout_user()
        st.session_state["auto_logout_msg"] = (
            f"{IDLE_HOURS}時間以上操作がなかったため、自動ログアウトしました。"
        )
        st.rerun()
    touch_activity()

def restore_auth_from_cookie(cookies):
    if not cookies:
        return False
    token = cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return False
    last = cookies.get(LAST_ACTIVE_COOKIE)
    if last:
        try:
            if time.time() - float(last) > IDLE_SECONDS:
                clear_auth_cookie()
                return False
        except ValueError:
            pass
    try:
        data = _auth_serializer().loads(token, max_age=SESSION_MAX_AGE_DAYS * 86400)
        st.session_state["logged_in_facility"] = data["facility"]
        st.session_state["current_user_name"] = data["name"]
        st.session_state["current_user_id"] = data.get("uid", "")
        if last:
            st.session_state["last_activity"] = float(last)
        touch_activity()
        return True
    except Exception:
        clear_auth_cookie()
        return False

def logout_user():
    st.session_state["logged_in_facility"] = None
    st.session_state["current_user_name"] = None
    st.session_state.pop("current_user_id", None)
    clear_auth_cookie()

# ==========================================
# ログイン認証
# ==========================================
def check_auth():
    if "logged_in_facility" not in st.session_state:
        st.session_state["logged_in_facility"] = None
    if "current_user_name" not in st.session_state:
        st.session_state["current_user_name"] = None

    if st.session_state["logged_in_facility"] is not None:
        return True

    cookies = read_browser_cookies()
    if cookies is None:
        st.stop()

    if restore_auth_from_cookie(cookies):
        return True

    if st.session_state.get("auto_logout_msg"):
        st.warning(st.session_state.pop("auto_logout_msg"))

    st.warning("miratech 琉球 医療機器管理システム")
    tab1, tab2 = st.tabs(["ログイン", "新規利用申請"])

    with tab1:
        with st.form("login_form"):
            st.info("セキュリティ保護のため、ログインが必要です。")
            input_id = st.text_input("ユーザーID")
            input_pass = st.text_input("パスワード", type="password")
            remember_me = st.checkbox(
                f"次回から自動ログイン（{IDLE_HOURS}時間操作がなければ自動ログアウト）",
                value=True,
            )
            
            if st.form_submit_button("ログイン", use_container_width=True):
                clean_id = input_id.strip()
                clean_pass = input_pass.strip()
                
                try:
                    conn = get_sheet_conn()
                    df_users = safe_read_worksheet(
                        conn, "ユーザー",
                        ["ユーザーID", "パスワード", "名前", "ステータス", "権限"],
                        raise_on_fail=True,
                    )
                    
                    clean_db_ids = clean_series(df_users["ユーザーID"])
                    user_row = df_users[clean_db_ids == clean_id]
                    
                    if not user_row.empty:
                        user_info = user_row.iloc[0]
                        saved_pass = clean_data_str(user_info["パスワード"])
                        saved_status = clean_data_str(user_info["ステータス"])
                        
                        if saved_pass == clean_pass:
                            if saved_status == "OK":
                                st.session_state["logged_in_facility"] = "miratech 琉球 管理センター"
                                st.session_state["current_user_name"] = clean_data_str(user_info["名前"])
                                st.session_state["current_user_id"] = clean_id
                                if remember_me:
                                    save_auth_cookie(clean_id, st.session_state["current_user_name"])
                                else:
                                    clear_auth_cookie()
                                    touch_activity()
                                
                                write_log(st.session_state["current_user_name"], "ログインしました")
                                st.rerun()
                                return True
                            else:
                                st.warning("現在、管理者の承認待ちです。許可が出るまでお待ちください。")
                        else:
                            st.error("パスワードが違います。")
                    else:
                        st.error("ユーザーIDが見つかりません。新規申請を行ってください。")
                except SheetReadError:
                    return False
                except Exception as e:
                    st.error(f"データベース接続エラー: {e}")

    with tab2:
        st.write("初めて利用される方は、こちらから利用申請を行ってください。")
        with st.form("register_form"):
            st.caption("**注意**: ユーザーIDとパスワードは **半角英数字のみ** で入力してください（漢字・ひらがな・カタカナ等は使用できません）。")
            
            new_id = st.text_input("希望するユーザーID", placeholder="例: user123")
            new_name = st.text_input("お名前（フルネーム）", placeholder="例: 安富 翔")
            new_pass = st.text_input("設定するパスワード", type="password", placeholder="例: pass456")
            
            if st.form_submit_button("利用申請を送信", use_container_width=True):
                if new_id and new_name and new_pass:
                    if not re.match(r'^[a-zA-Z0-9]+$', new_id) or not re.match(r'^[a-zA-Z0-9]+$', new_pass):
                        st.error("エラー: ユーザーIDとパスワードに日本語や記号が含まれています。「半角英数字のみ」で入力してやり直してください。")
                    else:
                        try:
                            conn = get_sheet_conn()
                            df_users = safe_read_worksheet(conn, "ユーザー", ["ユーザーID", "パスワード", "名前", "ステータス", "権限"])

                            if new_id in df_users["ユーザーID"].astype(str).values:
                                st.error("このIDは既に使われています。別のIDを指定してください。")
                            else:
                                new_user = pd.DataFrame([{
                                    "ユーザーID": new_id,
                                    "パスワード": new_pass,
                                    "名前": new_name,
                                    "ステータス": "未承認",
                                    "権限": "user" 
                                }])
                                updated_users = pd.concat([df_users, new_user], ignore_index=True)
                                conn.update(worksheet="ユーザー", data=updated_users)
                                write_log(new_name, f"新規利用申請を行いました (ID: {new_id})")
                                st.success(f"{new_name} さんの申請を受け付けました。管理者の承認をお待ちください。")
                        except Exception as e:
                            st.error(f"登録エラー: {e}")
                else:
                    st.error("すべての項目を入力してください。")

    return False

if not check_auth():
    st.stop()

enforce_idle_timeout()

# --- ログイン後の変数 ---
facility_name = st.session_state["logged_in_facility"]
url_me_no = st.query_params.get("me_no", "")
BASE_CATEGORIES = ["輸液ポンプ", "顕微鏡", "保育器", "分娩監視装置", "ネブライザー", "透視装置","無影灯","血圧計","超音波診断装置","超音波プローブ",
                   "ドプラ","検診台","血液ガス分析装置","吸引器類","加湿器類","分娩台","ベビーコット","哺乳瓶消毒器","煮沸消毒器","パルスオキシメーター",
                   "聴力検査器","光線治療器","酸素モニタ","電気メス","麻酔器","生体情報モニタ","心電計","手術台","子宮鏡","滅菌装置", "その他"]

# AI設定（ログイン後すぐに gRPC を読み込まないよう REST API は利用時のみ呼び出す）
try:
    conn = get_sheet_conn()
    df_master_global = safe_read_worksheet(conn, "機器マスター")
except Exception as e:
    st.error("Googleスプレッドシートに接続できません。Streamlit Cloud の Secrets 設定を確認してください。")
    st.caption(f"詳細: {e}")
    if st.button("ログアウトしてやり直す"):
        logout_user()
        st.rerun()
    st.stop()
# 機器マスターに登録済みの購入業者・機器種類を候補に反映
vendor_options = []
if not df_master_global.empty and "購入業者" in df_master_global.columns:
    vendor_options = sorted({
        clean_data_str(v) for v in df_master_global["購入業者"].unique()
        if clean_data_str(v)
    })

saved_categories = []
if not df_master_global.empty and "カテゴリ" in df_master_global.columns:
    saved_categories = sorted({
        clean_data_str(c) for c in df_master_global["カテゴリ"].unique()
        if clean_data_str(c) and clean_data_str(c) not in BASE_CATEGORIES
    })
category_options = sorted(set(BASE_CATEGORIES + saved_categories))

# ==========================================
# 【ルートB】QRコードを読み取った場合（故障報告 / 日常点検）
# ==========================================
if url_me_no:
    st.markdown(f"<h2 style='text-align: center; color: #FF4B4B;'>{facility_name}</h2>", unsafe_allow_html=True)

    qr_tab_fault, qr_tab_daily = st.tabs(["故障報告", "日常点検"])

    with qr_tab_fault:
        st.markdown("<h3 style='text-align: center;'>機器トラブル報告</h3>", unsafe_allow_html=True)
        st.success(f"対象機器: {url_me_no}")

        with st.form("nurse_report_form"):
            rep_date = st.date_input("発生日", value=date.today(), min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
            rep_dept = st.selectbox("あなたの部署", ["選択してください", "外来", "一般病棟", "オペ室"])
            rep_name = st.text_input("報告者名", value=st.session_state.get("current_user_name", ""))
            c1, c2 = st.columns(2)
            with c1:
                err_power = st.checkbox("電源不良")
                err_error = st.checkbox("エラー表示")
            with c2:
                err_alarm = st.checkbox("アラーム")
                err_drop = st.checkbox("落下・破損")
            rep_detail = st.text_area("詳細内容")

            if st.form_submit_button("報告を送信する", type="primary", use_container_width=True):
                symptoms = []
                if err_power: symptoms.append("電源不良")
                if err_error: symptoms.append("エラー表示")
                if err_alarm: symptoms.append("アラーム")
                if err_drop: symptoms.append("落下・破損")

                symptom_str = "、".join(symptoms)
                if rep_detail:
                    if symptom_str:
                        symptom_str += f" (詳細: {rep_detail})"
                    else:
                        symptom_str = f"その他 (詳細: {rep_detail})"
                elif not symptom_str:
                    symptom_str = "記載なし"

                try:
                    existing_data = safe_read_worksheet(conn, "故障報告", ["報告日", "発生日", "管理番号", "機種", "報告者", "部署", "症状", "対応状況"])

                    new_report = pd.DataFrame([{
                        "報告日": str(date.today()),
                        "発生日": str(rep_date),
                        "管理番号": url_me_no,
                        "機種": "不明な機器",
                        "報告者": rep_name,
                        "部署": rep_dept,
                        "症状": symptom_str,
                        "対応状況": "未対応"
                    }])

                    updated_df = pd.concat([existing_data, new_report], ignore_index=True)
                    conn.update(worksheet="故障報告", data=updated_df)

                    write_log(f"現場({rep_name})", f"{url_me_no} の故障報告を送信")

                    st.success("報告を受け付けました。ご協力ありがとうございます。")
                except Exception as e:
                    st.error(f"保存エラー: {e}")

    with qr_tab_daily:
        st.markdown("<h3 style='text-align: center;'>日常点検（動作点検）</h3>", unsafe_allow_html=True)
        render_daily_inspection_form(
            conn, df_master_global,
            initial_keyword=url_me_no,
            form_key_prefix="qr_daily",
            locked_keyword=True,
        )

    if st.button("ログアウト"):
        write_log(st.session_state["current_user_name"], "ログアウト")
        logout_user()
        st.query_params.clear()
        st.rerun()

    st.stop()

# ==========================================
# 【ルートA】直接アクセスした場合（管理画面へ）
# ==========================================
st.sidebar.success(f"ログイン中: {st.session_state.get('current_user_name', '不明')}")
st.sidebar.caption(f"App {APP_VERSION}")
if st.sidebar.button("ログアウト"):
    write_log(st.session_state["current_user_name"], "ログアウトしました")
    logout_user()
    st.rerun()

st.markdown(f"### {facility_name}")
st.title("医療機器点検・管理")

tab_names = ["点検入力", "日常点検", "マスター", "機器カルテ・実績", "管理番号シール", "新規機器登録", "修理故障・対応管理"]
tabs = st.tabs(tab_names)

# ====== タブ1：入力画面 ======
with tabs[0]:
    st.markdown("""
    <style>
    @media print {
        header, [data-testid="stSidebar"], footer { display: none !important; }
    }
    </style>
    """, unsafe_allow_html=True)

    if st.session_state.get("check_registered_msg"):
        st.success(st.session_state["check_registered_msg"])

    # エラー防止のためにすべての変数を初期化
    final_me_no = ""
    final_sn = ""
    device_category = "その他"
    device_model = ""
    scan_year_val = ""
    memo = ""
    result = "使用可"
    inspector = ""

    # 輸液・シリンジポンプ用項目の初期化
    chk_e1 = chk_e2 = chk_e3 = chk_e4 = chk_e5 = chk_e6 = chk_e7 = "---"

    # 保育器用項目の初期化
    inc_o_checks = dict(INCU_I_LEGACY_CHECKS)
    incu_i_checks = default_incu_i_checks()
    incu_i_measurements = default_incu_i_measurements()
    vsm_checks = default_vsm_checks()
    vsm_measurements = default_vsm_measurements()
    vsm_meta = default_vsm_meta()
    ecg_checks = default_ecg_checks()
    ecg_measurements = default_ecg_measurements()
    flow_acc = 0.0
    occ_press = 0.0
    bubble_ad_water = 100.0
    bubble_ad_dry = 10.0
    infusion_pump_checks = default_infusion_pump_checks()

    input_keyword = st.text_input(
        "管理番号・旧番号 または シリアルNo を入力して検索",
        placeholder="例: INP0001 または 旧番号",
        key="check_search_keyword",
    ).strip()

    if input_keyword != st.session_state.get("check_last_search_keyword", ""):
        st.session_state.pop("check_registered_msg", None)
        st.session_state.pop("pending_check_save", None)
        st.session_state.pop("inspection_saved_report", None)
        st.session_state["check_last_search_keyword"] = input_keyword

    if st.session_state.get("inspection_saved_report"):
        saved_inspection = st.session_state["inspection_saved_report"]
        st.markdown("---")
        st.subheader("点検報告書（印刷・PDF保存）")
        st.caption("Cmd/Ctrl + P で印刷、または「PDFをダウンロード」から保存できます。")
        render_inspection_report(
            saved_inspection["check_date"],
            saved_inspection["final_me_no"],
            saved_inspection["model_name"],
            saved_inspection["inspector"],
            saved_inspection["result"],
            saved_inspection.get("detail_text", ""),
            saved_inspection.get("memo", ""),
            device_category=saved_inspection.get("device_category", ""),
            report_kind=saved_inspection.get("report_kind", "定期点検"),
            unique_key_suffix="inspection_saved_session",
            item_rows=saved_inspection.get("item_rows"),
            check_type_label=saved_inspection.get("check_type", ""),
            report_sections=saved_inspection.get("report_sections"),
        )
        if st.button("次の点検入力へ", type="primary", key="inspection_report_done"):
            st.session_state.pop("inspection_saved_report", None)
            st.session_state.pop("check_registered_msg", None)
            st.rerun()
        st.markdown("---")

    master_row = None
    match_type = None
    if input_keyword and not df_master_global.empty:
        master_row, match_type = find_device_row(df_master_global, input_keyword)

    if master_row is not None:
        if match_type == "旧番号":
            st.info(
                f"旧番号「{clean_data_str(input_keyword)}」で見つかりました。"
                f" 現在の管理番号は {clean_data_str(master_row.get('管理番号', ''))} です。"
            )
        else:
            st.success("登録済みの機器が見つかりました。情報を自動出現させます。")
        final_me_no = clean_data_str(master_row.get("管理番号", ""))
        final_sn = clean_data_str(master_row.get("シリアルNo", ""))
        device_category = clean_data_str(master_row.get("カテゴリ", "その他"))
        device_model = normalize_stored_model(device_category, master_row.get("機種", ""))
        scan_year_val = clean_data_str(
            master_row.get("製造年月日", "") or master_row.get("製造年", "")
        )

        sync_device_display_fields("check", final_me_no, device_category, final_sn, device_model)

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.text_input("管理番号", value=final_me_no, disabled=True, key="check_disp_me")
            st.text_input("機器の種類", value=device_category, disabled=True, key="check_disp_cat")
        with col_m2:
            st.text_input("シリアルNo", value=final_sn, disabled=True, key="check_disp_sn")
            st.text_input("型式", value=device_model, disabled=True, key="check_disp_model")

        # 型式別の基準値を自動セット
        min_flow, max_flow = 18.0, 22.0
        min_press, max_press = 30.0, 90.0
        flow_unit, press_unit = "ml", "kPa"

        if "TE-331" in device_model or "TE-351" in device_model or "TE-371" in device_model or "TE-381" in device_model:
            min_flow, max_flow = 19.4, 20.6
            min_press, max_press = 53.4, 80.0
        elif "TE-171" in device_model:
            min_flow, max_flow = 19.0, 21.0
            min_press, max_press = 6.0, 60.0
            press_unit = "秒"
        elif "TE-LM830" in device_model:
            min_flow, max_flow = 18.0, 22.0
            min_press, max_press = 30.0, 120.0
        elif "OT-707" in device_model or "OT-818G" in device_model:
            min_flow, max_flow = 18.0, 22.0
            min_press, max_press = 30.0, 140.0
        elif "AS-800" in device_model:
            min_flow, max_flow = 9.0, 11.0
            min_press, max_press = 0.0, 2.0
            press_unit = "分"

        st.markdown("---")

        if "last_check_date" not in st.session_state:
            st.session_state["last_check_date"] = date.today()

        saved_report = None
        with st.form("check_form"):
            check_type = st.radio("点検区分", ["院内点検(miratech)", "メーカー点検", "メーカー修理・校正"], horizontal=True)
            check_date = st.date_input("作業日", value=st.session_state["last_check_date"])
            inspector = st.text_input("実施者", value=st.session_state.get("current_user_name", ""))

            if check_type == "院内点検(miratech)":
                st.caption(INSPECTION_CHECK_LEGEND)
                if device_category == "輸液ポンプ":
                    st.write("**1. 外観・作動点検**")
                    col1, col2 = st.columns(2)
                    with col1:
                        chk_e1 = st.radio("本体の汚れ・破損なし", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e2 = st.radio("ポールクランプ用ネジ穴", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e3 = st.radio("チューブクランプ動作", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e4 = st.radio("フィンガー部動作", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                    with col2:
                        chk_e5 = st.radio("AC・DC切り替え", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e6 = st.radio("セルフチェック機能", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e7 = st.radio("表示部LED", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)

                    st.write("**2. 警報・作動点検**")
                    alarm_col1, alarm_col2 = st.columns(2)
                    for idx, label in enumerate(INFUSION_PUMP_ALARM_ITEMS):
                        target_col = alarm_col1 if idx % 2 == 0 else alarm_col2
                        with target_col:
                            infusion_pump_checks[label] = st.radio(
                                label, INSPECTION_CHECK_OPTIONS, horizontal=True, index=None,
                                key=f"inp_alarm_{label}",
                            )

                    st.write("**3. 機能・設定点検**")
                    func_col1, func_col2 = st.columns(2)
                    for idx, label in enumerate(INFUSION_PUMP_FUNCTION_ITEMS):
                        target_col = func_col1 if idx % 2 == 0 else func_col2
                        with target_col:
                            infusion_pump_checks[label] = st.radio(
                                label, INSPECTION_CHECK_OPTIONS, horizontal=True, index=None,
                                key=f"inp_func_{label}",
                            )

                    st.write("**4. 数値・精度チェック**")
                    col_num1, col_num2 = st.columns(2)
                    with col_num1:
                        st.caption("流量精度 ※流量120ml/hr・10min・予定量20ml・許容範囲18～22ml")
                        st.info(f"基準値：{min_flow} ～ {max_flow} {flow_unit}")
                        flow_acc = st.number_input(f"流量精度 ({flow_unit})", value=20.0, step=0.1)
                    with col_num2:
                        st.caption("閉塞検出 ※流量120ml/h・「M」30～90kPa")
                        st.info(f"基準値：{min_press} ～ {max_press} {press_unit}")
                        occ_press = st.number_input(f"閉塞検出 ({press_unit})", value=60.0, step=1.0)

                    st.caption("気泡センサーAD値 ※水入り輸液セット100以上・水無し輸液セット10以下")
                    bubble_col1, bubble_col2 = st.columns(2)
                    with bubble_col1:
                        bubble_ad_water = st.number_input("水入り", min_value=0.0, value=100.0, step=1.0)
                    with bubble_col2:
                        bubble_ad_dry = st.number_input("水無し", min_value=0.0, value=10.0, step=1.0)

                elif device_category == "シリンジポンプ":
                    st.write("**1. 外観・作動点検**")
                    col1, col2 = st.columns(2)
                    with col1:
                        chk_e1 = st.radio("本体の汚れ・破損なし", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e2 = st.radio("ポールクランプ用ネジ穴", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e3 = st.radio("チューブクランプ動作", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e4 = st.radio("フィンガー部動作", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                    with col2:
                        chk_e5 = st.radio("AC・DC切り替え", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e6 = st.radio("セルフチェック機能", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        chk_e7 = st.radio("表示部LED", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)

                    st.write("**2. 数値・精度チェック**")
                    col_num1, col_num2 = st.columns(2)
                    with col_num1:
                        st.info(f"基準値：{min_flow} ～ {max_flow} {flow_unit}")
                        flow_acc = st.number_input(f"流量精度 ({flow_unit})", value=float(max_flow + min_flow) / 2, step=0.1)
                    with col_num2:
                        st.info(f"基準値：{min_press} ～ {max_press} {press_unit}")
                        occ_press = st.number_input(f"閉塞検出 ({press_unit})", value=float(max_press + min_press) / 2, step=1.0)

                elif device_category == "保育器":
                    if is_incu_i_incubator(device_category, device_model):
                        render_incu_i_inspection_fields(incu_i_checks, incu_i_measurements)
                    else:
                        st.write("**2. 各種警報機能**")
                        o3, o4 = st.columns(2)
                        with o3:
                            inc_o_checks["チェックスイッチ"] = st.radio("チェックスイッチ作動", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["設定温度警報(マニュアル)"] = st.radio("設定温度警報(マニュアル)", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["設定温度警報(皮膚温)"] = st.radio("設定温度警報(皮膚温)", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        with o4:
                            inc_o_checks["プローブ警報"] = st.radio("プローブ警報作動", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["停電警報"] = st.radio("停電警報作動", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["キャノピ傾斜"] = st.radio("キャノピ傾斜動作", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)

                        st.write("**3. 蘇生装置・酸素・外装**")
                        o5, o6 = st.columns(2)
                        with o5:
                            inc_o_checks["蘇生装置"] = st.radio("蘇生装置の機能点検・異常なし", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["酸素ブレンダ作動"] = st.radio("酸素ブレンダ作動確認", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["供給ガス警報"] = st.radio("供給ガスが発生するか", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                        with o6:
                            inc_o_checks["吸引・流量計"] = st.radio("吸引ユニット・酸素流量計正常", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["外装・キャノピ・ネジ類"] = st.radio("支柱・キャノピ・反射板・ネジ等", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)
                            inc_o_checks["電源・ジャック・ガード"] = st.radio("電源コード・各種ジャック・ガード", INSPECTION_CHECK_OPTIONS, horizontal=True, index=None)

                elif device_category == "生体情報モニタ":
                    render_vsm_inspection_fields(vsm_checks, vsm_measurements, vsm_meta)

                elif device_category == "心電計":
                    render_ecg_inspection_fields(ecg_checks, ecg_measurements)
            else:
                st.info("外部対応のため数値測定はスキップされます。")

            st.markdown("---")
            result = st.radio("総合評価", ["使用可", "メーカー修理", "廃棄"], horizontal=True)
            memo = st.text_area("備考・報告欄", placeholder="特記事項があれば記入してください")

            submitted = st.form_submit_button("保存・決定", type="primary", use_container_width=True)

        if submitted:
            if not final_me_no:
                st.warning("管理番号が入力されていません。")
            elif not inspector.strip():
                st.warning("実施者を入力してください。")
            else:
                ng_items, incomplete_items = validate_inspection_items(
                    device_category, check_type, result, inc_o_checks,
                    chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                    flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                    flow_unit, press_unit,
                    infusion_pump_checks=infusion_pump_checks,
                    bubble_ad_water=bubble_ad_water,
                    bubble_ad_dry=bubble_ad_dry,
                    device_model=device_model,
                    incu_i_checks=incu_i_checks,
                    incu_i_measurements=incu_i_measurements,
                    vsm_checks=vsm_checks,
                    vsm_measurements=vsm_measurements,
                    vsm_meta=vsm_meta,
                    ecg_checks=ecg_checks,
                    ecg_measurements=ecg_measurements,
                )
                detail_text, item_rows, report_sections = build_inspection_save_bundle(
                    check_type, device_category, result, inc_o_checks,
                    chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                    flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                    flow_unit, press_unit,
                    infusion_pump_checks=infusion_pump_checks,
                    bubble_ad_water=bubble_ad_water,
                    bubble_ad_dry=bubble_ad_dry,
                    device_model=device_model,
                    incu_i_checks=incu_i_checks,
                    incu_i_measurements=incu_i_measurements,
                    vsm_checks=vsm_checks,
                    vsm_measurements=vsm_measurements,
                    vsm_meta=vsm_meta,
                    ecg_checks=ecg_checks,
                    ecg_measurements=ecg_measurements,
                )
                save_payload = {
                    "final_me_no": final_me_no,
                    "final_sn": final_sn,
                    "device_category": device_category,
                    "device_model": device_model,
                    "scan_year_val": scan_year_val,
                    "check_date": check_date,
                    "check_type": check_type,
                    "inspector": inspector,
                    "result": result,
                    "memo": memo,
                    "detail_text": detail_text,
                    "item_rows": item_rows,
                    "report_sections": report_sections,
                    "incomplete_items": incomplete_items,
                    "ng_items": ng_items,
                }

                if incomplete_items:
                    st.error(f"未選択の項目があります。{INSPECTION_CHECK_LEGEND.replace('判定: ', '')} のいずれかを選択してください。")
                    st.warning("未設定の項目: " + "、".join(incomplete_items))
                    st.session_state["pending_check_save"] = save_payload
                elif ng_items and check_type == "院内点検(miratech)" and result == "使用可":
                    st.error("不合格(×)・修理検討(△)または基準外の測定値があります。")
                    st.warning("該当項目: " + "、".join(ng_items))
                    st.session_state.pop("pending_check_save", None)
                    st.error("総合評価が「使用可」のため保存できません。数値・項目を修正するか、総合評価を【メーカー修理】等に変更してください。")
                else:
                    if ng_items:
                        st.warning("問題項目: " + "、".join(ng_items))
                        if check_type == "院内点検(miratech)" and result != "使用可":
                            st.info("総合評価が「使用可」以外のため、問題項目があっても保存します。")
                    st.session_state.pop("pending_check_save", None)
                    try:
                        with st.spinner("スプレッドシートに保存しています..."):
                            saved_report = execute_inspection_save(
                                conn,
                                **{k: v for k, v in save_payload.items() if k not in ("incomplete_items", "ng_items")},
                            )
                        st.session_state["inspection_saved_report"] = saved_report
                        st.success(st.session_state["check_registered_msg"])
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

        pending = st.session_state.get("pending_check_save")
        if pending:
            st.markdown("---")
            st.warning("未設定の項目があります。保存しますか？")
            st.write("未設定の項目: " + "、".join(pending.get("incomplete_items", [])))
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes（保存する）", type="primary", use_container_width=True, key="confirm_incomplete_save_yes"):
                    try:
                        with st.spinner("スプレッドシートに保存しています..."):
                            saved_report = execute_inspection_save(
                                conn,
                                **{k: v for k, v in pending.items() if k not in ("incomplete_items", "ng_items")},
                            )
                        st.session_state["inspection_saved_report"] = saved_report
                        st.session_state.pop("pending_check_save", None)
                        st.success(st.session_state["check_registered_msg"])
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")
            with col_no:
                if st.button("No（キャンセル）", use_container_width=True, key="confirm_incomplete_save_no"):
                    st.session_state.pop("pending_check_save", None)
                    st.info("保存をキャンセルしました。未設定の項目を入力してください。")
                    st.rerun()

# ====== タブ2：日常点検 ======
with tabs[1]:
    st.subheader("日常点検（動作点検）")
    st.caption("対象: 超音波診断装置・保育器（毎日の動作確認）")
    daily_input_tab, daily_print_tab = st.tabs(["点検入力", "月次PDF・印刷"])
    with daily_input_tab:
        render_daily_inspection_form(conn, df_master_global, form_key_prefix="admin_daily")
    with daily_print_tab:
        render_monthly_daily_inspection_export(conn, df_master_global, facility_name)

# ====== タブ3：マスター ======
with tabs[2]:
    st.subheader("機器台帳 ＆ データ管理")
    
    sub_m1, sub_m2 = st.tabs(["資産統計 ＆ 一覧表示", "登録データの修正・変更"])

    with sub_m1:
        try:
            df_m_stats = safe_read_worksheet(conn, "機器マスター")
                
            if not df_m_stats.empty and "カテゴリ" in df_m_stats.columns:
                st.markdown("#### 現在の院内保有台数サマリー")
                total_devices = len(df_m_stats)
                
                cat_counts = df_m_stats["カテゴリ"].value_counts().reset_index()
                cat_counts.columns = ["機器カテゴリー", "保有台数（台）"]
                cat_counts = cat_counts.sort_values("保有台数（台）", ascending=False)
                
                col_stat1, col_stat2 = st.columns([1, 2])
                with col_stat1:
                    st.metric("総管理機器数", f"{total_devices} 台")
                    st.dataframe(_sanitize_dataframe(cat_counts), hide_index=True, use_container_width=True)
                with col_stat2:
                    st.bar_chart(cat_counts, x="機器カテゴリー", y="保有台数（台）", color="#ff9f43")
                st.markdown("---")
                
        except Exception as e:
            st.error(f"統計データの集計中にエラーが発生しました: {e}")
            
        st.markdown("#### 各種シートの詳細表示")
        view_cat_master = st.selectbox("表示するシートを切り替え", ["機器マスター", "点検履歴", "故障報告"], key="master_cat")
        if st.button("台帳データを最新にする"):
            st.cache_data.clear()
            
        try:
            df = safe_read_worksheet(conn, view_cat_master)
            if df.empty:
                st.info(f"「{view_cat_master}」シートにはまだデータがありません。")
            else:
                display_dataframe(df, hide_index=True, use_container_width=True)
        except Exception as e:
            st.error(f"接続エラー: {e}")

    with sub_m2:
        st.markdown("#### 機器データの修正")
        st.write("管理番号を入力すると現在のデータが呼び出され、内容を上書き修正できます。")

        edit_me_no = st.text_input(
            "修正したい機器の「管理番号・旧番号」を入力",
            placeholder="例: INP0001",
            key="edit_me_input",
        ).strip()

        if edit_me_no:
            try:
                df_master_edit = safe_read_worksheet(conn, "機器マスター")
                target_row, match_type = find_device_row(df_master_edit, edit_me_no)

                if target_row is not None:
                    clean_edit_me_no = clean_data_str(target_row.get("管理番号", ""))
                    master_me_nos = clean_series(df_master_edit["管理番号"])

                    with st.form("edit_master_form"):
                        if match_type == "旧番号":
                            st.info(f"旧番号「{clean_data_str(edit_me_no)}」で見つかりました（現在の管理番号: {clean_edit_me_no}）")
                        else:
                            st.info(f"{clean_edit_me_no} のデータを修正します。直したい箇所を書き換えて「保存」を押してください。")
                        
                        new_cat = st.text_input("カテゴリ", value=clean_data_str(target_row.get("カテゴリ", "")))
                        new_model = st.text_input("型式 (例: ACCURO)", value=normalize_stored_model(
                            target_row.get("カテゴリ", ""), target_row.get("機種", "")
                        ))
                        new_sn = st.text_input("シリアルNo", value=clean_data_str(target_row.get("シリアルNo", "")))
                        new_legacy = st.text_input(
                            "旧番号（複数はカンマ区切り）",
                            value=clean_data_str(target_row.get("旧番号", "") or target_row.get("旧管理番号", "")),
                            placeholder="例: ME-123, OLD456",
                        )
                        new_year = st.text_input("製造年月日", value=clean_data_str(target_row.get("製造年月日", "")))
                        
                        new_location = st.text_input("設置場所", value=clean_data_str(target_row.get("設置場所", "")))
                        new_vendor = st.text_input("購入業者", value=clean_data_str(target_row.get("購入業者", "")))

                        saved_acq = clean_data_str(target_row.get("導入形態", "購入"))
                        acq_options = ["購入", "リース", "レンタル", "その他"]
                        if saved_acq not in acq_options: acq_options.append(saved_acq)
                        new_acq_type = st.selectbox("導入形態", acq_options, index=acq_options.index(saved_acq))
                        
                        new_price = st.text_input("購入金額(円)", value=clean_data_str(target_row.get("購入金額", "")))

                        saved_delivery_str = clean_data_str(target_row.get("納入日", ""))
                        try:
                            saved_delivery_date = pd.to_datetime(saved_delivery_str).date()
                        except:
                            saved_delivery_date = date.today()
                        new_delivery = st.date_input("購入日", value=saved_delivery_date, min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))

                        if st.form_submit_button("変更を上書き保存する", type="primary"):
                            safe_new_sn = protect_zeros(new_sn)

                            mask_m = master_me_nos == clean_edit_me_no
                            df_master_edit.loc[mask_m, "カテゴリ"] = new_cat
                            df_master_edit.loc[mask_m, "機種"] = model_for_spreadsheet(new_model)
                            df_master_edit.loc[mask_m, "シリアルNo"] = safe_new_sn
                            df_master_edit.loc[mask_m, "製造年月日"] = new_year
                            df_master_edit.loc[mask_m, "設置場所"] = new_location
                            df_master_edit.loc[mask_m, "購入業者"] = new_vendor
                            df_master_edit.loc[mask_m, "導入形態"] = new_acq_type
                            df_master_edit.loc[mask_m, "購入金額"] = new_price
                            df_master_edit.loc[mask_m, "納入日"] = str(new_delivery)
                            if "旧番号" not in df_master_edit.columns:
                                df_master_edit["旧番号"] = ""
                            df_master_edit.loc[mask_m, "旧番号"] = clean_data_str(new_legacy)
                            conn.update(worksheet="機器マスター", data=df_master_edit)

                            try:
                                df_hist_edit = safe_read_worksheet(conn, "点検履歴")
                                if not df_hist_edit.empty and "管理番号" in df_hist_edit.columns:
                                    clean_hist_me = clean_series(df_hist_edit["管理番号"])
                                    mask_h = clean_hist_me == clean_edit_me_no
                                    if mask_h.any():
                                        df_hist_edit.loc[mask_h, "カテゴリ"] = new_cat
                                        df_hist_edit.loc[mask_h, "機種"] = model_for_spreadsheet(new_model)
                                        df_hist_edit.loc[mask_h, "シリアルNo"] = safe_new_sn
                                        df_hist_edit.loc[mask_h, "製造年月日"] = new_year
                                        conn.update(worksheet="点検履歴", data=df_hist_edit)
                            except Exception:
                                pass 
                            
                            st.cache_data.clear() 
                            st.success(f"{clean_edit_me_no} のデータを最新に修正し、過去の履歴にも完全に同期しました！")
                            write_log(st.session_state.get("current_user_name", "管理者"), f"{clean_edit_me_no} のデータを修正・同期")
                else:
                    st.warning("指定された管理番号・旧番号は登録されていません。")
            except Exception as e:
                st.error(f"データ取得エラー: {e}")

# ====== タブ4：機器カルテ・実績 ======
with tabs[3]:
    st.subheader("機器カルテ照合 ＆ 日次実績")
    
    if st.button("最新のデータを読み込む", key="refresh_history_tab"):
        st.cache_data.clear()
        
    try:
        df_master = safe_read_worksheet(conn, "機器マスター")
        df_history = safe_read_worksheet(conn, "点検履歴", INSPECTION_HISTORY_COLUMNS)
        df_fault_reports = safe_read_worksheet(conn, "故障報告", FAULT_REPORT_COLUMNS)

        sub_tab1, sub_tab2, sub_tab3 = st.tabs([
            "機器カルテ（ワンタッチ照合）",
            "日次点検実績（グラフ）",
            "点検表履歴・印刷",
        ])

        with sub_tab1:
            karte_keyword = st.text_input(
                "管理番号・旧番号 または シリアルNo を入力して検索",
                placeholder="例: INP0001 または 旧番号",
                key="karte_search_keyword",
            ).strip()

            if not df_master.empty and "管理番号" in df_master.columns:
                master_row, match_type = find_device_row(df_master, karte_keyword) if karte_keyword else (None, None)

                if master_row is not None:
                    if match_type == "旧番号":
                        st.info(
                            f"旧番号「{clean_data_str(karte_keyword)}」で見つかりました。"
                            f" 現在の管理番号は {clean_data_str(master_row.get('管理番号', ''))} です。"
                        )
                    else:
                        st.success("登録済みの機器が見つかりました。")

                    target_me = clean_data_str(master_row.get("管理番号", ""))
                    model_name = normalize_stored_model(
                        master_row.get("カテゴリ", ""),
                        master_row.get("機種", ""),
                    ) or "不明な機器"
                    device_category = clean_data_str(master_row.get("カテゴリ", ""))
                    device_model = normalize_stored_model(device_category, master_row.get("機種", ""))

                    sync_device_display_fields(
                        "karte",
                        target_me,
                        device_category,
                        master_row.get("シリアルNo", ""),
                        device_model,
                    )

                    col_k1, col_k2 = st.columns(2)
                    with col_k1:
                        st.text_input("管理番号", value=target_me, disabled=True, key="karte_disp_me")
                        st.text_input("機器の種類", value=device_category, disabled=True, key="karte_disp_cat")
                    with col_k2:
                        st.text_input("シリアルNo", value=clean_data_str(master_row.get("シリアルNo", "")), disabled=True, key="karte_disp_sn")
                        st.text_input("型式", value=device_model, disabled=True, key="karte_disp_model")

                    st.markdown("---")
                    st.markdown(f"### {model_name} (管理番号: {target_me}) のカルテ")

                    hist_df = filter_history_for_device(df_history, target_me, master_row)
                    fault_df = filter_fault_reports_for_device(df_fault_reports, target_me, master_row)
                    has_hist = not hist_df.empty
                    has_fault = not fault_df.empty

                    if has_fault:
                        st.write("#### 故障報告履歴")
                        st.dataframe(
                            _sanitize_dataframe(fault_df),
                            use_container_width=True,
                            hide_index=True,
                        )

                    if has_hist:
                        if has_fault:
                            st.markdown("---")
                        st.write("#### 過去の点検・修理履歴")
                        hist_display = hist_df.copy()
                        hist_display["点検区分"] = hist_display.apply(
                            lambda r: classify_inspection_type(
                                get_history_detail_text(r), r.get("備考", ""), r.get("判定", ""),
                            ),
                            axis=1,
                        )
                        st.dataframe(_sanitize_dataframe(hist_display), use_container_width=True, hide_index=True)

                        st.markdown("---")
                        st.write("#### 点検結果履歴（報告書表示・印刷）")
                        st.caption("定期点検・修理対応の報告書をPDF保存または Cmd/Ctrl + P で印刷できます。")

                        record_labels = {}
                        for list_index, (row_idx, row) in enumerate(hist_df.iterrows()):
                            record_labels[_history_record_label(row, list_index)] = row_idx

                        selected_label = st.selectbox(
                            "表示したい点検表を選択",
                            list(record_labels.keys()),
                            key=f"history_report_select_{target_me}",
                        )

                        if selected_label:
                            report_data = hist_df.loc[record_labels[selected_label]]
                            report_detail, report_items, report_check_type, report_sections = resolve_history_inspection(report_data)
                            report_model = normalize_stored_model(
                                report_data.get("カテゴリ", ""),
                                report_data.get("機種", model_name),
                            ) or model_name
                            report_kind = classify_inspection_type(
                                report_detail,
                                report_data.get("備考", ""),
                                report_data.get("判定", ""),
                            )
                            render_inspection_report(
                                report_data.get("点検日", ""),
                                target_me,
                                report_model,
                                report_data.get("実施者", "-"),
                                report_data.get("判定", "-"),
                                report_detail,
                                report_data.get("備考", ""),
                                device_category=clean_data_str(report_data.get("カテゴリ", device_category)),
                                report_kind=report_kind,
                                unique_key_suffix=f"karte_{record_labels[selected_label]}",
                                item_rows=report_items,
                                check_type_label=report_check_type,
                                report_sections=report_sections,
                            )
                    elif not has_fault:
                        st.info("この機器の点検・修理履歴はありません。")
                elif karte_keyword:
                    st.warning("該当する機器が見つかりません。管理番号・旧番号・シリアルNo を確認してください。")
            else:
                st.info("機器マスターにまだデータがありません。")

        with sub_tab2:
            if not df_history.empty and "点検日" in df_history.columns:
                df_history["点検日"] = df_history["点検日"].astype(str)
                st.markdown("#### 日別点検件数の推移")
                
                daily_counts = df_history["点検日"].value_counts().reset_index()
                daily_counts.columns = ["点検日", "点検件数（台）"]
                daily_counts = daily_counts.sort_values("点検日")
                
                col_graph, col_table = st.columns([2, 1])
                
                with col_graph:
                    st.write("日別別の点検台数グラフ")
                    st.bar_chart(daily_counts, x="点検日", y="点検件数（台）", color="#2e86de")
                    
                with col_table:
                    st.write("日付ごとの合計台数")
                    display_dataframe(daily_counts.iloc[::-1], use_container_width=True, hide_index=True)

                st.markdown("##### 特定の日の点検内訳を確認する")
                target_date = st.date_input("確認したい日付を選択", date.today())
                
                day_detail_df = df_history[df_history["点検日"] == str(target_date)]
                if not day_detail_df.empty:
                    st.success(f"{target_date} は 合計 {len(day_detail_df)} 台 の点検が完了しています。")
                    display_dataframe(day_detail_df, use_container_width=True, hide_index=True)
                else:
                    st.info(f"選択された日付（{target_date}）の点検データはありません。")
            else:
                st.info("集計できる点検履歴データがまだありません。")

        with sub_tab3:
            render_inspection_history_viewer(conn, df_master, df_history)

    except Exception as e:
        st.error(f"システムエラー: {e}")

# ====== タブ5：QRコード・管理番号シール ======
with tabs[4]:
    st.subheader("管理番号シール ＆ QRコード")
    st.write("管理番号を入力すると、テプラ用の管理番号シールを作成できます。")

    df_m_qr = safe_read_worksheet(conn, "機器マスター")
    for field_key in ("sticker_model", "sticker_serial", "sticker_me_display", "sticker_delivery"):
        st.session_state.setdefault(field_key, "")

    target_qr_me = st.text_input("管理番号・旧番号を入力", placeholder="例: INP0001", key="sticker_me_no")
    master_info = lookup_device_for_sticker(df_m_qr, target_qr_me) if target_qr_me.strip() else {}
    apply_sticker_master_lookup(target_qr_me, master_info)

    if master_info:
        if master_info.get("matched_via") == "旧番号":
            st.info(
                f"旧番号「{clean_data_str(target_qr_me)}」で見つかりました。"
                f" シールの管理番号は {master_info.get('me_no', '')} です。"
            )
        else:
            st.info("機器マスターから情報を読み込みました。")
    elif target_qr_me.strip():
        st.warning(
            f"「{clean_data_str(target_qr_me)}」は機器マスターに見つかりません。"
            " 管理番号・旧番号を確認するか、手入力してください。"
        )

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sticker_model = st.text_input(
            "機種名",
            placeholder="例: ACCURO",
            key="sticker_model",
        )
        sticker_serial = st.text_input(
            "シリアルNo",
            placeholder="例: 12345678",
            key="sticker_serial",
        )
    with col_s2:
        sticker_me = st.text_input(
            "管理番号（表示用）",
            key="sticker_me_display",
        )
        sticker_delivery = st.text_input(
            "購入日",
            placeholder="例: 2024-03-15",
            key="sticker_delivery",
        )

    if st.button("管理番号シールを作成する", type="primary", use_container_width=True):
        if not sticker_me.strip():
            st.warning("管理番号を入力してください。")
        else:
            st.session_state["sticker_preview"] = {
                "model_name": sticker_model,
                "me_no": sticker_me,
                "serial_no": sticker_serial,
                "delivery_date": sticker_delivery,
            }

    if st.session_state.get("sticker_preview"):
        s = st.session_state["sticker_preview"]
        st.markdown("---")
        render_sticker_workflow(
            s["model_name"], s["me_no"], s["serial_no"], s["delivery_date"],
            button_key="tepra_qr_tab",
        )

# ====== タブ6：新規機器の登録 ======
with tabs[5]:
    st.subheader("新規機器の直接登録")
    st.write("ここで登録した機器データは、直接「機器マスター」へ保存されます。点検は登録後に「点検入力」タブで行えます。")
    
    reg_mode = st.radio("入力方法を選択してください", ["AI銘板スキャナー", "手動で情報を入力"], horizontal=True)
    
    # ページ上部で読み込み済みのマスターを再利用（二重読込を避けて表示を高速化）
    df_m_reg = df_master_global
    history_categories = []
    if not df_m_reg.empty and "カテゴリ" in df_m_reg.columns:
        history_categories = sorted({clean_data_str(c) for c in df_m_reg["カテゴリ"].unique() if clean_data_str(c)})
    history_vendors = []
    if not df_m_reg.empty and "購入業者" in df_m_reg.columns:
        history_vendors = sorted({clean_data_str(v) for v in df_m_reg["購入業者"].unique() if clean_data_str(v)})

    if reg_mode == "AI銘板スキャナー":
        st.info("銘板写真を選び、「AIで銘板を読み取る」を押すと型式などを自動入力します。")
        _render_ai_nameplate_scanner()
            
        if st.session_state.get("scan_model") is not None:
            st.success("AIの読み取りが完了しました！以下の内容を確認し、追加情報を入れて登録してください。")

    # 共通の登録フォーム
    show_form = True
    if reg_mode == "AI銘板スキャナー" and st.session_state.get("scan_model") is None:
        show_form = False 

    if show_form:
        with st.form("direct_reg_form"):
            man_me_no = st.text_input("1. 管理番号 (必須)", placeholder="例: Y0001")
            man_legacy_me = st.text_input(
                "1b. 旧番号（任意・複数はカンマ区切り）",
                placeholder="例: ME-123, OLD456",
            )
            
            st.write("2. 機器種類（カテゴリ）※必須")
            sel_cat = st.selectbox(" 過去のリストから選ぶ", [""] + history_categories)
            txt_cat = st.text_input(" リストにない場合はここに直接入力", placeholder="例: 新しいポンプ")
            
            st.write("3. 購入業者")
            sel_vendor = st.selectbox("過去のリストから選ぶ", [""] + history_vendors)
            txt_vendor = st.text_input("リストにない場合はここに直接入力", placeholder="例: 〇〇医療器")
            
            st.markdown("---")
            man_maker = st.text_input("4. メーカー", placeholder="例: テルモ")
            man_model = st.text_input("5. 型式 (機種)", value=st.session_state.get("scan_model", ""), placeholder="例: TE-131A")
            man_sn = st.text_input("6. シリアルNo", value=st.session_state.get("scan_sn", ""), placeholder="例: 12345678")
            man_year = st.text_input("7. 製造年月日", value=st.session_state.get("scan_year", ""), placeholder="例: 2014")
            man_life = st.number_input("8. 耐用年数（年）", min_value=0, value=6, step=1)
            
            man_location = st.text_input("9. 設置場所", placeholder="例: 一般病棟")
            man_acq_type = st.selectbox("10. 導入形態", ["購入", "リース", "レンタル", "その他"])
            man_price = st.text_input("11. 購入金額", placeholder="例: 1500000")
            man_delivery = st.date_input("12. 購入日", value=date.today(), min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
            
            if st.form_submit_button("機器マスターに登録する", type="primary"):
                final_cat = txt_cat if txt_cat.strip() != "" else sel_cat
                final_vendor = txt_vendor if txt_vendor.strip() != "" else sel_vendor

                if not man_me_no or not final_cat or not clean_data_str(man_model):
                    st.error("管理番号・機器種類・型式 は必須です！")
                else:
                    final_cat = clean_data_str(final_cat)
                    final_vendor = clean_data_str(final_vendor)
                    try:
                        # "ME No." ではなく "管理番号" を探すように変更
                        clean_db_me_reg = clean_series(df_m_reg["管理番号"])
                        
                        if clean_data_str(man_me_no) in clean_db_me_reg.values:
                            # エラーメッセージの "ME No." も "管理番号" に変更
                            st.error(f"{man_me_no} は既に登録されています。別の管理番号を指定してください。")
                        else:
                            new_master_row = pd.DataFrame([{
                                "管理番号": protect_zeros(man_me_no),
                                "旧番号": clean_data_str(man_legacy_me),
                                "カテゴリ": final_cat,
                                "メーカー": man_maker,
                                "機種": model_for_spreadsheet(man_model),
                                "シリアルNo": protect_zeros(man_sn),
                                "製造年": man_year,
                                "耐用年数": man_life,
                                "設置場所": man_location,
                                "購入業者": final_vendor,
                                "導入形態": man_acq_type,
                                "購入金額": man_price,
                                "納入日": str(man_delivery),
                                "最終点検日": "", "最終判定": "", "最終実施者": ""
                            }])
                            updated_master_reg = pd.concat([df_m_reg, new_master_row], ignore_index=True)
                            conn.update(worksheet="機器マスター", data=updated_master_reg)
                            
                            write_log(st.session_state.get("current_user_name", "管理者"), f"{man_me_no} を新規登録")
                            st.session_state["last_registered_sticker"] = {
                                "model_name": model_for_spreadsheet(man_model),
                                "me_no": clean_data_str(man_me_no),
                                "serial_no": clean_data_str(man_sn),
                                "delivery_date": str(man_delivery),
                            }
                            st.session_state["scan_model"] = None
                            st.session_state["scan_sn"] = None
                            st.session_state["scan_year"] = None
                            st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    if st.session_state.get("last_registered_sticker"):
        s = st.session_state["last_registered_sticker"]
        st.markdown("---")
        st.success(f"「{s['me_no']}」を登録しました！ 管理番号シールを印刷できます。")
        render_sticker_workflow(
            s["model_name"], s["me_no"], s["serial_no"], s["delivery_date"],
            button_key="tepra_after_reg",
        )
        if st.button("シール表示を閉じる", key="close_reg_sticker"):
            st.session_state.pop("last_registered_sticker", None)
            st.rerun()

# ====== タブ7：修理故障・対応管理 ======
with tabs[6]:
    render_repair_fault_management(conn)
