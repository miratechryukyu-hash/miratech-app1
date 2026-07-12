import streamlit as st
import streamlit.components.v1 as components
import extra_streamlit_components as stx
import gspread
from gspread_dataframe import get_as_dataframe, set_with_dataframe
import pandas as pd
from datetime import datetime, date, timedelta
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
from pathlib import Path

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
APP_VERSION = "2026-07-12g"

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

def analyze_nameplate_with_gemini(image_bytes, mime_type="image/jpeg"):
    """Gemini REST API で銘板画像を解析（gRPC ライブラリを使わず Cloud でも安全）"""
    api_key = _get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY が設定されていません")

    prompt = """
    この医療機器の銘板写真から以下の情報を抜き出して、JSON形式で回答してください。
    キーは以下のようにしてください:
    - model (型式)
    - serial_number (製造番号/SN)
    - manufacture_year (製造年月日。例: 2018.10.10)
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode()}},
            ]
        }]
    }
    resp = requests.post(url, json=payload, timeout=90)
    if resp.status_code == 400:
        raise ValueError(f"Gemini API リクエストエラー: {resp.text[:300]}")
    if resp.status_code in (401, 403):
        raise ValueError(
            "Gemini API Key が無効です。Streamlit Cloud の Secrets の "
            "GEMINI_API_KEY を Google AI Studio で発行したキーに差し替えてください。"
        )
    resp.raise_for_status()
    body = resp.json()
    return body["candidates"][0]["content"]["parts"][0]["text"]

st.set_page_config(page_title="miratech 医療機器管理システム", layout="centered")

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
    clean = df.copy()
    for col in clean.columns:
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

def lookup_device_for_sticker(df_master, me_no):
    if df_master.empty or "管理番号" not in df_master.columns:
        return {}
    clean_me = clean_data_str(me_no)
    matched = df_master[clean_series(df_master["管理番号"]) == clean_me]
    if matched.empty:
        return {}
    row = matched.iloc[0]
    return {
        "model_name": normalize_stored_model(row.get("カテゴリ", ""), row.get("機種", "")),
        "me_no": clean_me,
        "serial_no": clean_data_str(row.get("シリアルNo", "")),
        "delivery_date": clean_data_str(row.get("購入日", "") or row.get("納入日", "") or row.get("納品日", "")),
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
    st.session_state["sticker_me_display"] = lookup_key
    if master_info:
        st.session_state["sticker_model"] = master_info.get("model_name", "")
        st.session_state["sticker_serial"] = master_info.get("serial_no", "")
        st.session_state["sticker_delivery"] = master_info.get("delivery_date", "")

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
    item_names, item_results, item_judges = [], [], []
    if not detail_text or str(detail_text).strip().lower() in ("", "nan"):
        return item_names, item_results, item_judges
    for p in str(detail_text).split("|"):
        p = p.strip()
        if not p or "基準流量" in p or "基準閉塞" in p:
            continue
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        item_names.append(k.strip())
        if "(" in v and ")" in v:
            val, jdg = v.rsplit("(", 1)
            item_results.append(val.strip())
            item_judges.append(jdg.replace(")", "").strip())
        else:
            item_results.append(v.strip())
            item_judges.append(v.strip())
    return item_names, item_results, item_judges

def render_inspection_report(check_date, me_no, model_name, inspector, result, detail_text="", memo=""):
    st.write(f"## 医療機器定期点検報告書 （{check_date} 実施分）")
    info_df = pd.DataFrame({
        "管理番号": [me_no],
        "機種(型式)": [model_name],
        "点検実施者": [inspector],
        "総合評価": [result],
    })
    st.table(info_df)

    item_names, item_results, item_judges = parse_detail_text_to_table(detail_text)
    if item_names:
        excel_df = pd.DataFrame({
            "点検・測定項目": item_names,
            "点検実測値 / 結果": item_results,
            "判定": item_judges,
        })
        st.table(excel_df)

    if memo and str(memo).strip().lower() not in ("", "nan"):
        st.info(f"備考・処置内容:\n{memo}")

    st.info("キーボードの「Ctrl + P」（Macは「Cmd + P」）を押すと、この表だけが綺麗に印刷されます。")

def save_inspection_to_sheets(conn, final_me_no, final_sn, device_category, device_model,
                              scan_year_val, check_date, check_type, inspector, result,
                              memo, detail_text):
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

    history_columns = ["点検日", "管理番号", "カテゴリ", "シリアルNo", "製造年月日", "機種", "実施者", "判定", "詳細データ", "備考"]
    existing_history = safe_read_worksheet(conn, "点検履歴", history_columns)
    if existing_history.empty:
        existing_history = pd.DataFrame(columns=history_columns)

    new_hist_row = {
        "点検日": str(check_date),
        "管理番号": protect_zeros(final_me_no),
        "カテゴリ": device_category,
        "シリアルNo": protect_zeros(final_sn),
        "製造年月日": scan_year_val,
        "機種": model_for_spreadsheet(device_model),
        "実施者": inspector,
        "判定": result,
        "詳細データ": detail_text,
        "備考": memo,
    }
    for col in existing_history.columns:
        if col not in new_hist_row:
            new_hist_row[col] = ""

    new_hist_df = pd.DataFrame([new_hist_row])
    updated_history = pd.concat([existing_history, new_hist_df[existing_history.columns]], ignore_index=True)
    conn.update(worksheet="点検履歴", data=updated_history)

def validate_inspection_items(device_category, check_type, result, inc_o_checks,
                              chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                              flow_acc, occ_press, min_flow, max_flow, min_press, max_press):
    """点検項目のNG・未入力を検出する。戻り値: (ng_items, incomplete_items)"""
    ng_items = []
    incomplete_items = []

    if check_type != "院内点検(miratech)":
        return ng_items, incomplete_items

    if device_category in ["輸液ポンプ", "シリンジポンプ"]:
        pump_checks = {
            "本体の汚れ・破損なし": chk_e1,
            "ポールクランプ用ネジ穴": chk_e2,
            "チューブクランプ動作": chk_e3,
            "フィンガー部動作": chk_e4,
            "AC・DC切り替え": chk_e5,
            "セルフチェック機能": chk_e6,
            "表示部LED": chk_e7,
        }
        for label, val in pump_checks.items():
            if is_unselected(val):
                incomplete_items.append(label)
            elif val == "NG":
                ng_items.append(label)

        if result == "使用可":
            if not (min_flow <= flow_acc <= max_flow):
                ng_items.append(f"流量精度実測値（{flow_acc}）")
            if not (min_press <= occ_press <= max_press):
                ng_items.append(f"閉塞検出圧実測値（{occ_press}）")

    elif device_category == "保育器":
        for label, val in inc_o_checks.items():
            if is_unselected(val):
                incomplete_items.append(label)
            elif val == "NG":
                ng_items.append(label)

    return ng_items, incomplete_items

def is_unselected(val):
    return val in ("--", "---", None, "")

def build_inspection_detail_text(check_type, device_category, result, inc_o_checks,
                                 chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                                 flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                                 flow_unit, press_unit):
    parts_list = []
    if check_type == "院内点検(miratech)":
        if device_category in ["輸液ポンプ", "シリンジポンプ"]:
            parts_list.extend([
                f"本体の汚れ・破損なし:{chk_e1}", f"ポールクランプ用ネジ穴:{chk_e2}",
                f"チューブクランプ動作:{chk_e3}", f"フィンガー部動作:{chk_e4}",
                f"AC・DC切り替え:{chk_e5}", f"セルフチェック機能:{chk_e6}", f"表示部LED:{chk_e7}"
            ])
            flow_judge = "OK" if (min_flow <= flow_acc <= max_flow) else "NG"
            press_judge = "OK" if (min_press <= occ_press <= max_press) else "NG"
            parts_list.extend([
                f"流量精度実測値:{flow_acc} {flow_unit} ({flow_judge})",
                f"閉塞検出圧実測値:{occ_press} {press_unit} ({press_judge})",
                f"基準流量:{min_flow}～{max_flow}",
                f"基準閉塞:{min_press}～{max_press} {press_unit}"
            ])
        elif device_category == "保育器":
            for k, v in inc_o_checks.items():
                parts_list.append(f"{k}:{v}")

    detail_text = " | ".join(parts_list)
    if check_type != "院内点検(miratech)":
        detail_text = f"点検区分:{check_type}" + (f" | {detail_text}" if detail_text else "")
    return detail_text

def execute_inspection_save(conn, final_me_no, final_sn, device_category, device_model,
                            scan_year_val, check_date, check_type, inspector, result,
                            memo, detail_text):
    save_inspection_to_sheets(
        conn, final_me_no, final_sn, device_category, device_model,
        scan_year_val, check_date, check_type, inspector, result,
        memo, detail_text,
    )
    write_log(inspector, f"{final_me_no} の点検を登録")
    st.session_state["last_check_date"] = check_date
    st.session_state["check_registered_msg"] = f"{final_me_no} の点検データを登録しました。"
    return {
        "check_date": check_date,
        "final_me_no": final_me_no,
        "model_name": model_for_spreadsheet(device_model),
        "inspector": inspector,
        "result": result,
        "detail_text": detail_text,
        "memo": memo,
    }

# --- ログ書き込み用共通関数 ---
def write_log(user_name, action):
    try:
        conn = get_sheet_conn()
        df_logs = safe_read_worksheet(conn, "アクセスログ", ["日時", "ユーザー名", "アクション"])
        
        new_log = pd.DataFrame([{
            "日時": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        expires_at=datetime.now() + timedelta(days=SESSION_MAX_AGE_DAYS),
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
        expires_at=datetime.now() + timedelta(days=SESSION_MAX_AGE_DAYS),
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
                   "聴力検査器","光線治療器","酸素モニタ","電気メス","麻酔器","生体情報モニタ","手術台","子宮鏡","滅菌装置", "その他"]

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
# 【ルートB】QRコードを読み取った場合（トラブル報告画面へ直行）
# ==========================================
if url_me_no:
    st.markdown(f"<h2 style='text-align: center; color: #FF4B4B;'>{facility_name}</h2>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>機器トラブル報告システム</h3>", unsafe_allow_html=True)
    
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

tab_names = ["点検入力", "マスター", "機器カルテ・実績", "管理番号シール", "新規機器登録", "ユーザー・ログ管理"]
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
    inc_o_checks = {
        "チェックスイッチ": "---", "設定温度警報(マニュアル)": "---", "設定温度警報(皮膚温)": "---",
        "プローブ警報": "---", "停電警報": "---", "キャノピ傾斜": "---",
        "蘇生装置": "---", "酸素ブレンダ作動": "---", "供給ガス警報": "---",
        "吸引・流量計": "---", "外装・キャノピ・ネジ類": "---", "電源・ジャック・ガード": "---"
    }
    flow_acc = 0.0
    occ_press = 0.0

    input_keyword = st.text_input(
        "管理番号 または シリアルNo を入力して検索",
        placeholder="例: INP0001",
        key="check_search_keyword",
    ).strip()

    if input_keyword != st.session_state.get("check_last_search_keyword", ""):
        st.session_state.pop("check_registered_msg", None)
        st.session_state.pop("pending_check_save", None)
        st.session_state["check_last_search_keyword"] = input_keyword

    master_row = None
    if (
        input_keyword
        and not df_master_global.empty
        and "管理番号" in df_master_global.columns
        and "シリアルNo" in df_master_global.columns
    ):
        clean_keyword = clean_data_str(input_keyword)
        clean_db_me = clean_series(df_master_global["管理番号"])
        clean_db_sn = clean_series(df_master_global["シリアルNo"])

        matched_me = df_master_global[clean_db_me == clean_keyword]
        if not matched_me.empty:
            master_row = matched_me.iloc[0]
        else:
            matched_sn = df_master_global[clean_db_sn == clean_keyword]
            if not matched_sn.empty:
                master_row = matched_sn.iloc[0]

    if master_row is not None:
        st.success("登録済みの機器が見つかりました。情報を自動出現させます。")
        final_me_no = clean_data_str(master_row.get("管理番号", ""))
        final_sn = clean_data_str(master_row.get("シリアルNo", ""))
        device_category = clean_data_str(master_row.get("カテゴリ", "その他"))
        device_model = normalize_stored_model(device_category, master_row.get("機種", ""))
        scan_year_val = clean_data_str(
            master_row.get("製造年月日", "") or master_row.get("製造年", "")
        )

        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.text_input("管理番号", value=final_me_no, disabled=True)
            st.text_input("機器の種類", value=device_category, disabled=True)
        with col_m2:
            st.text_input("シリアルNo", value=final_sn, disabled=True)
            st.text_input("型式", value=device_model, disabled=True)

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
                if device_category in ["輸液ポンプ", "シリンジポンプ"]:
                    st.write("**1. 外観・作動点検**")
                    col1, col2 = st.columns(2)
                    with col1:
                        chk_e1 = st.radio("本体の汚れ・破損なし", ["OK", "NG", "---"], horizontal=True, index=None)
                        chk_e2 = st.radio("ポールクランプ用ネジ穴", ["OK", "NG", "---"], horizontal=True, index=None)
                        chk_e3 = st.radio("チューブクランプ動作", ["OK", "NG", "---"], horizontal=True, index=None)
                        chk_e4 = st.radio("フィンガー部動作", ["OK", "NG", "---"], horizontal=True, index=None)
                    with col2:
                        chk_e5 = st.radio("AC・DC切り替え", ["OK", "NG", "---"], horizontal=True, index=None)
                        chk_e6 = st.radio("セルフチェック機能", ["OK", "NG", "---"], horizontal=True, index=None)
                        chk_e7 = st.radio("表示部LED", ["OK", "NG", "---"], horizontal=True, index=None)

                    st.write("**2. 数値・精度チェック**")
                    col_num1, col_num2 = st.columns(2)
                    with col_num1:
                        st.info(f"基準値：{min_flow} ～ {max_flow} {flow_unit}")
                        flow_acc = st.number_input(f"流量精度実測値 ({flow_unit})", value=float(max_flow+min_flow)/2, step=0.1)
                    with col_num2:
                        st.info(f"基準値：{min_press} ～ {max_press} {press_unit}")
                        occ_press = st.number_input(f"閉塞検出圧実測値 ({press_unit})", value=float(max_press+min_press)/2, step=1.0)

                elif device_category == "保育器":
                    st.write("**2. 各種警報機能**")
                    o3, o4 = st.columns(2)
                    with o3:
                        inc_o_checks["チェックスイッチ"] = st.radio("チェックスイッチ作動", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["設定温度警報(マニュアル)"] = st.radio("設定温度警報(マニュアル)", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["設定温度警報(皮膚温)"] = st.radio("設定温度警報(皮膚温)", ["OK", "NG", "---"], horizontal=True, index=None)
                    with o4:
                        inc_o_checks["プローブ警報"] = st.radio("プローブ警報作動", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["停電警報"] = st.radio("停電警報作動", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["キャノピ傾斜"] = st.radio("キャノピ傾斜動作", ["OK", "NG", "---"], horizontal=True, index=None)

                    st.write("**3. 蘇生装置・酸素・外装**")
                    o5, o6 = st.columns(2)
                    with o5:
                        inc_o_checks["蘇生装置"] = st.radio("蘇生装置の機能点検・異常なし", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["酸素ブレンダ作動"] = st.radio("酸素ブレンダ作動確認", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["供給ガス警報"] = st.radio("供給ガスが発生するか", ["OK", "NG", "---"], horizontal=True, index=None)
                    with o6:
                        inc_o_checks["吸引・流量計"] = st.radio("吸引ユニット・酸素流量計正常", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["外装・キャノピ・ネジ類"] = st.radio("支柱・キャノピ・反射板・ネジ等", ["OK", "NG", "---"], horizontal=True, index=None)
                        inc_o_checks["電源・ジャック・ガード"] = st.radio("電源コード・各種ジャック・ガード", ["OK", "NG", "---"], horizontal=True, index=None)
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
                )
                detail_text = build_inspection_detail_text(
                    check_type, device_category, result, inc_o_checks,
                    chk_e1, chk_e2, chk_e3, chk_e4, chk_e5, chk_e6, chk_e7,
                    flow_acc, occ_press, min_flow, max_flow, min_press, max_press,
                    flow_unit, press_unit,
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
                    "incomplete_items": incomplete_items,
                    "ng_items": ng_items,
                }

                if incomplete_items:
                    st.error("未選択ですよ。OK / NG / --- のいずれかを選択してください。")
                    st.warning("未設定の項目: " + "、".join(incomplete_items))
                    st.session_state["pending_check_save"] = save_payload
                elif ng_items and check_type == "院内点検(miratech)" and result == "使用可":
                    st.error("NG項目があります。")
                    st.warning("NGの項目: " + "、".join(ng_items))
                    st.session_state.pop("pending_check_save", None)
                    st.error("総合評価が「使用可」のため保存できません。数値・項目を修正するか、総合評価を【メーカー修理】等に変更してください。")
                else:
                    if ng_items:
                        st.warning("NG項目があります: " + "、".join(ng_items))
                        if check_type == "院内点検(miratech)" and result != "使用可":
                            st.info("総合評価が「使用可」以外のため、NG項目があっても保存します。")
                    st.session_state.pop("pending_check_save", None)
                    try:
                        with st.spinner("スプレッドシートに保存しています..."):
                            saved_report = execute_inspection_save(conn, **{k: v for k, v in save_payload.items() if k not in ("incomplete_items", "ng_items")})
                        st.success(st.session_state["check_registered_msg"])
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
                                **{k: v for k, v in pending.items() if k not in ("incomplete_items", "ng_items")}
                            )
                        st.session_state.pop("pending_check_save", None)
                        st.success(st.session_state["check_registered_msg"])
                    except Exception as e:
                        st.error(f"登録エラー: {e}")
            with col_no:
                if st.button("No（キャンセル）", use_container_width=True, key="confirm_incomplete_save_no"):
                    st.session_state.pop("pending_check_save", None)
                    st.info("保存をキャンセルしました。未設定の項目を入力してください。")
                    st.rerun()

        if saved_report:
            render_inspection_report(
                saved_report["check_date"],
                saved_report["final_me_no"],
                saved_report["model_name"],
                saved_report["inspector"],
                saved_report["result"],
                saved_report["detail_text"],
                saved_report["memo"],
            )

# ====== タブ2：マスター ======
with tabs[1]:
    st.subheader("機器台帳 ＆ データ管理")
    
    # サブタブに「故障対応・修理入力」を追加して3つに拡張
    sub_m1, sub_m2, sub_m3 = st.tabs(["資産統計 ＆ 一覧表示", "登録データの修正・変更", "故障対応・修理入力"])

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

        edit_me_no = st.text_input("修正したい機器の「管理番号」を入力", placeholder="例: INP0001", key="edit_me_input").strip()

        if edit_me_no:
            try:
                df_master_edit = safe_read_worksheet(conn, "機器マスター")

                clean_edit_me_no = clean_data_str(edit_me_no)
                master_me_nos = clean_series(df_master_edit["管理番号"])

                if not df_master_edit.empty and clean_edit_me_no in master_me_nos.values:
                    target_row = df_master_edit[master_me_nos == clean_edit_me_no].iloc[0]

                    with st.form("edit_master_form"):
                        st.info(f"{clean_edit_me_no} のデータを修正します。直したい箇所を書き換えて「保存」を押してください。")
                        
                        new_cat = st.text_input("カテゴリ", value=clean_data_str(target_row.get("カテゴリ", "")))
                        new_model = st.text_input("型式 (例: ACCURO)", value=normalize_stored_model(
                            target_row.get("カテゴリ", ""), target_row.get("機種", "")
                        ))
                        new_sn = st.text_input("シリアルNo", value=clean_data_str(target_row.get("シリアルNo", "")))
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
                    st.warning("指定された 管理番号 は登録されていません。")
            except Exception as e:
                st.error(f"データ取得エラー: {e}")

    # 【新機能】未対応の故障報告の一覧から修理・点検・報告書生成を一括で行う
    with sub_m3:
        st.markdown("#### 故障対応・修理完了の入力")
        st.write("現場から上がった故障報告に対して、修理対応と安全点検の結果を入力します。")

        try:
            df_failed = safe_read_worksheet(conn, "故障報告")
            
            if df_failed.empty:
                st.info("現在、故障報告データはありません。")
            elif "対応状況" not in df_failed.columns:
                st.warning("故障報告シートに「対応状況」列がありません。")
            else:
                # 「対応状況」が未対応のものだけを抽出
                df_pending = df_failed[df_failed["対応状況"].astype(str).str.strip() == "未対応"]
                
                if df_pending.empty:
                    st.success("現在、対応待ちの故障報告はありません。すべての修理・点検が完了しています！")
                else:
                    st.warning(f"現在、**{len(df_pending)} 件** の未対応の故障報告があります。")
                    
                    # どの故障対応を行うか選択するプルダウン
                    pending_options = df_pending.apply(
                        lambda r: f"{r['管理番号']} - {r['機種']} ({r['部署']} / 症状: {r['症状']}) 報告日: {r['報告日']}", axis=1
                    ).tolist()
                    
                    selected_job = st.selectbox("対応する故障報告を選択してください", pending_options)
                    
                    # 選択された行のインデックスとデータを特定
                    selected_idx = df_pending.index[pending_options.index(selected_job)]
                    job_data = df_failed.loc[selected_idx]
                    target_me = job_data["管理番号"]
                    
                    with st.form("repair_form"):
                        st.info(f"対象機器: {target_me} の修理対応・点検結果を入力します。")
                        
                        repair_date = st.date_input("対応完了日（現場点検日）", value=date.today())
                        repair_detail = st.text_area("修理・処置内容", placeholder="例: 包包交換、内部清掃、設定リセット実施")
                        
                        st.write("修理後の安全点検チェック（エビデンス確保）")
                        chk_r1 = st.checkbox("外観点検（汚れ、破損、変形がないこと）", value=True)
                        chk_r2 = st.checkbox("作動点検（基本動作、セルフチェックが正常なこと）", value=True)
                        chk_r3 = st.checkbox("警報点検（アラーム、シミュレータテスト正常なこと）", value=True)
                        
                        repair_result = st.radio("総合評価", ["使用可", "メーカー修理依頼", "廃棄手続き"], horizontal=True)
                        repair_memo = st.text_area("備考（特記事項があれば）")
                        
                        submit_repair = st.form_submit_button("修理・点検完了を確定する", type="primary")
                    
                    if submit_repair:
                        # 1. 故障報告シートのステータスを更新
                        df_failed.at[selected_idx, "対応状況"] = f"対応済 ({repair_date})"
                        conn.update(worksheet="故障報告", data=df_failed)
                        
                        # 2. 点検履歴シートに修理点検エビデンスを1行追加
                        df_history = safe_read_worksheet(conn, "点検履歴")
                        
                        # チェック状況を文字化
                        chk_str = f"外観:{'〇' if chk_r1 else '×'}, 作動:{'〇' if chk_r2 else '×'}, 警報:{'〇' if chk_r3 else '×'}"
                        detail_text = f"【故障修理後点検】 処置: {repair_detail} / 安全確認: {chk_str}"
                        
                        # 機器マスターから現在の基本情報を引っ張ってくる
                        df_m_lookup = safe_read_worksheet(conn, "機器マスター")
                        m_row = df_m_lookup[clean_series(df_m_lookup["管理番号"]) == clean_data_str(target_me)]
                        device_category = "その他"
                        scan_year_val = ""
                        if not m_row.empty:
                            device_category = clean_data_str(m_row.iloc[0].get("カテゴリ", "その他"))
                            scan_year_val = clean_data_str(m_row.iloc[0].get("製造年月日", ""))
                        
                        history_dict = {
                            "点検日": str(repair_date), 
                            "管理番号": protect_zeros(target_me), 
                            "カテゴリ": device_category,
                            "シリアルNo": protect_zeros(job_data.get("シリアルNo", "")), 
                            "製造年月日": scan_year_val, 
                            "機種": job_data["機種"], 
                            "実施者": st.session_state.get("current_user_name", "ME"), 
                            "判定": repair_result, 
                            "詳細データ": detail_text,
                            "備考": f"元故障症状: {job_data['症状']} / 備考: {repair_memo}"
                        }
                        df_history = pd.concat([df_history, pd.DataFrame([history_dict])], ignore_index=True)
                        conn.update(worksheet="点検履歴", data=df_history)
                        
                        # 3. 機器マスターの最終点検情報も更新
                        if not df_m_lookup.empty:
                            mask_m = clean_series(df_m_lookup["管理番号"]) == clean_data_str(target_me)
                            if mask_m.any():
                                df_m_lookup.loc[mask_m, "最終点検日"] = str(repair_date)
                                df_m_lookup.loc[mask_m, "最終判定"] = f"{repair_result}(故障対応)"
                                df_m_lookup.loc[mask_m, "最終実施者"] = st.session_state.get("current_user_name", "ME")
                                conn.update(worksheet="機器マスター", data=df_m_lookup)
                        
                        st.cache_data.clear()
                        st.success(f"{target_me} の修理対応・安全点検の記録を保存し、台帳を更新しました！")
                        write_log(st.session_state.get("current_user_name", "ME"), f"{target_me} の故障対応・修理点検を完了")
                        
                        # 4. その場で即座に印刷・PDF化できる「修理・点検報告書」を画面に出現させる
                        st.markdown("---")
                        st.subheader("提出用 報告書の印刷レイアウト")
                        
                        html_report = f"""
                        <div style="padding: 30px; border: 2px solid #333; background-color: white; color: black; border-radius: 5px; font-family: sans-serif;">
                            <h2 style="text-align: center; border-bottom: 2px solid black; padding-bottom: 10px; margin-top:0;">医療機器 修理・点検完了報告書</h2>
                            <div style="display: flex; justify-content: space-between; margin-bottom: 20px;">
                                <div><b>提出先:</b> 現場責任者 / 看護師長 殿</div>
                                <div><b>完了報告日:</b> {repair_date}</div>
                            </div>
                            <table style="width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 20px;">
                                <tr>
                                    <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>管理番号</b></td>
                                    <td style="padding: 10px; border: 1px solid #aaa; width: 25%;">{target_me}</td>
                                    <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>対象機種</b></td>
                                    <td style="padding: 10px; border: 1px solid #aaa; width: 25%;">{job_data['機種']}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>故障発生部署</b></td>
                                    <td style="padding: 10px; border: 1px solid #aaa;">{job_data['部署']}</td>
                                    <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>初期報告者</b></td>
                                    <td style="padding: 10px; border: 1px solid #aaa;">{job_data['報告者']}</td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>現場報告の症状</b></td>
                                    <td colspan="3" style="padding: 10px; border: 1px solid #aaa;">{job_data['症状']}</td>
                                </tr>
                            </table>
                            
                            <h4 style="border-left: 4px solid #333; padding-left: 8px; margin-bottom: 10px;">■ 修理・処置内容</h4>
                            <div style="padding: 10px; border: 1px solid #aaa; min-height: 50px; margin-bottom: 20px; background-color: #fafafa;">
                                {repair_detail}
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
                                    <td style="padding: 10px; border: 1px solid #aaa; font-size: 16px; color: red; font-weight: bold;">{repair_result}</td>
                                    <td style="padding: 10px; border: 1px solid #aaa; width: 25%; background-color: #f0f0f0;"><b>点検技術者（実施者）</b></td>
                                    <td style="padding: 10px; border: 1px solid #aaa; text-align: center;">{st.session_state.get("current_user_name", "ME")} (印)</td>
                                </tr>
                                <tr>
                                    <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>施設側 収領・確認印</b></td>
                                    <td colspan="3" style="padding: 25px; border: 1px solid #aaa; text-align: right; color: #ccc;">確認日: &nbsp;&nbsp;&nbsp;&nbsp;年 &nbsp;&nbsp;&nbsp;月 &nbsp;&nbsp;&nbsp;日 &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; サイン / 職印欄: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</td>
                                </tr>
                            </table>
                            <p style="text-align: right; font-size: 11px; color: gray; margin-top: 15px; margin-bottom: 0;">技術管理・保守責任: miratech 琉球 医療機器管理システム</p>
                        </div>
                        """
                        st.markdown(html_report, unsafe_allow_html=True)
                        st.info("このまま紙に印刷またはPDF化する場合は、ブラウザの印刷機能（Ctrl + P 又は Cmd + P）を実行してください。自動的にキレイなA4報告書枠のみが印刷されます。")
                        st.button("次の対応入力をする（画面をリフレッシュ）")

        except Exception as e:
            st.error(f"故障データの処理中にエラーが発生しました: {e}")

# ====== タブ3：機器カルテ・実績 ======
with tabs[2]:
    st.subheader("機器カルテ照合 ＆ 日次実績")
    
    if st.button("最新のデータを読み込む", key="refresh_history_tab"):
        st.cache_data.clear()
        
    try:
        df_master = safe_read_worksheet(conn, "機器マスター")
        df_history = safe_read_worksheet(conn, "点検履歴")

        sub_tab1, sub_tab2 = st.tabs(["機器カルテ（ワンタッチ照合）", "日次点検実績（グラフ）"])

        with sub_tab1:
            st.write("一覧から機器を選んでください")
            if not df_master.empty and "管理番号" in df_master.columns:
                machine_labels = []
                machine_rows = []
                for _, row in df_master.iterrows():
                    me = clean_data_str(row.get("管理番号", "不明"))
                    cat = clean_data_str(row.get("カテゴリ", ""))
                    model = normalize_stored_model(cat, row.get("機種", "")) or "不明な機器"
                    machine_labels.append(f"{me} | {model}")
                    machine_rows.append(row)

                display_dataframe(df_master, hide_index=True, use_container_width=True)

                selected_label = st.selectbox(
                    "詳細を見たい機器を選択",
                    [""] + machine_labels,
                    key="karte_machine_select",
                )

                if selected_label:
                    idx = machine_labels.index(selected_label)
                    target_me = clean_data_str(machine_rows[idx].get("管理番号", "不明"))
                    model_name = normalize_stored_model(
                        machine_rows[idx].get("カテゴリ", ""),
                        machine_rows[idx].get("機種", ""),
                    ) or "不明な機器"
                    
                    st.markdown("---")
                    st.markdown(f"### {model_name} (管理番号: {target_me}) のカルテ")
                    
                    hist_df = pd.DataFrame()
                    if not df_history.empty and "管理番号" in df_history.columns:
                        clean_hist_search_me = clean_series(df_history["管理番号"])
                        hist_df = df_history[clean_hist_search_me == target_me].iloc[::-1]
                        
                    if not hist_df.empty:
                        st.write("#### 過去の点検・修理履歴")
                        st.dataframe(_sanitize_dataframe(hist_df), use_container_width=True, hide_index=True)
                        
                        st.markdown("---")
                        st.write("#### 点検結果履歴（報告書表示）")
                        st.write("履歴から特定の日の点検報告書を、点検入力タブと同じ形式で表示・印刷できます。")

                        st.markdown("""
                        <style>
                        @media print {
                            header, [data-testid="stSidebar"], footer { display: none !important; }
                        }
                        </style>
                        """, unsafe_allow_html=True)

                        selected_date = st.selectbox(
                            "表示したい点検日を選択してください",
                            hist_df["点検日"].tolist(),
                            key=f"history_report_date_{target_me}",
                        )

                        if selected_date:
                            report_data = hist_df[hist_df["点検日"] == selected_date].iloc[0]
                            report_model = normalize_stored_model(
                                report_data.get("カテゴリ", ""),
                                report_data.get("機種", model_name),
                            ) or model_name
                            render_inspection_report(
                                report_data.get("点検日", selected_date),
                                target_me,
                                report_model,
                                report_data.get("実施者", "-"),
                                report_data.get("判定", "-"),
                                report_data.get("詳細データ", ""),
                                report_data.get("備考", ""),
                            )
                    else:
                        st.info("この機器の点検・修理履歴はありません。")
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

    except Exception as e:
        st.error(f"システムエラー: {e}")

# ====== タブ4：QRコード・管理番号シール ======
with tabs[3]:
    st.subheader("管理番号シール ＆ QRコード")
    st.write("管理番号を入力すると、テプラ用の管理番号シールを作成できます。")

    df_m_qr = safe_read_worksheet(conn, "機器マスター")
    for field_key in ("sticker_model", "sticker_serial", "sticker_me_display", "sticker_delivery"):
        st.session_state.setdefault(field_key, "")

    target_qr_me = st.text_input("管理番号を入力", placeholder="例: INP0001", key="sticker_me_no")
    master_info = lookup_device_for_sticker(df_m_qr, target_qr_me) if target_qr_me.strip() else {}
    apply_sticker_master_lookup(target_qr_me, master_info)

    if master_info:
        st.info("機器マスターから情報を読み込みました。")
    elif target_qr_me.strip():
        st.warning(
            f"管理番号「{clean_data_str(target_qr_me)}」は機器マスターに見つかりません。"
            " 手入力するか、管理番号の表記（例: INP0001）を確認してください。"
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

# ====== タブ5：新規機器の登録 ======
with tabs[4]:
    st.subheader("新規機器の直接登録")
    st.write("ここで登録した機器データは、直接「機器マスター」へ保存されます。点検は登録後に「点検入力」タブで行えます。")
    
    reg_mode = st.radio("入力方法を選択してください", ["AI銘板スキャナー", "手動で情報を入力"], horizontal=True)
    
    # 既存データから候補を自動生成
    df_m_reg = safe_read_worksheet(conn, "機器マスター", ["管理番号", "カテゴリ", "購入業者"])
    history_categories = []
    if not df_m_reg.empty and "カテゴリ" in df_m_reg.columns:
        history_categories = sorted({clean_data_str(c) for c in df_m_reg["カテゴリ"].unique() if clean_data_str(c)})
    history_vendors = []
    if not df_m_reg.empty and "購入業者" in df_m_reg.columns:
        history_vendors = sorted({clean_data_str(v) for v in df_m_reg["購入業者"].unique() if clean_data_str(v)})

    if reg_mode == "AI銘板スキャナー":
        st.info("新しい機器の銘板を撮影すると、AIが情報を読み取ってくれます。")
        st.caption(_gemini_key_status_message())
        if not _get_gemini_api_key():
            st.error(
                "AI銘板スキャナーを使うには、Streamlit Cloud の Secrets に "
                "GEMINI_API_KEY を追加してください。"
            )
            st.markdown(
                "**Secrets の書き方（`[connections.gsheets]` の外・先頭付近に追加）:**"
            )
            st.code(
                'GEMINI_API_KEY = "AIzaSy..."  # Google AI Studio の API キー\n\n'
                "[connections.gsheets]\n"
                'spreadsheet = "スプレッドシートID"\n'
                "# ... 以下 gsheets 設定 ...",
                language="toml",
            )
            st.markdown(
                "保存後は **Manage app → Reboot app** を実行してください。"
                " ローカルで試す場合は `.streamlit/secrets.toml` に同じ行を追加します。"
            )
        else:
            st.caption("銘板写真を撮影または選択してください（iPhone・iPad・Android 対応）")
            try:
                img_file = back_camera_input(key="ai_camera", height=560)
            except Exception as e:
                st.warning(f"カメラ機能でエラーが発生しました。下のファイル選択をお使いください。（{e}）")
                img_file = _upload_fallback_camera(key="ai_camera_fallback", height=560)
            
            if img_file:
                current_image_bytes = img_file.getvalue()
                if st.session_state.get("last_scanned_image") != current_image_bytes:
                    with st.spinner("AIが文字を解析しています（約10秒）..."):
                        try:
                            img = Image.open(BytesIO(current_image_bytes))
                            fmt = (img.format or "JPEG").upper()
                            mime_type = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}.get(fmt, "image/jpeg")
                            response_text = analyze_nameplate_with_gemini(current_image_bytes, mime_type)
                            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                            if json_match:
                                data = json.loads(json_match.group())
                                
                                st.session_state["scan_model"] = data.get("model", "")
                                st.session_state["scan_sn"] = data.get("serial_number", "")
                                st.session_state["scan_year"] = data.get("manufacture_year", "")
                                st.session_state["last_scanned_image"] = current_image_bytes
                                st.rerun() 
                            else:
                                st.warning("文字が見つかりませんでした。ブレていないか確認してもう一度撮影してください。")
                        except Exception as e:
                            st.error(f"システムエラー: {e}")
            
            if st.session_state.get("scan_model") is not None:
                st.success("AIの読み取りが完了しました！以下の内容を確認し、追加情報を入れて登録してください。")

    # 共通の登録フォーム
    show_form = True
    if reg_mode == "AI銘板スキャナー" and st.session_state.get("scan_model") is None:
        show_form = False 

    if show_form:
        with st.form("direct_reg_form"):
            man_me_no = st.text_input("1. 管理番号 (必須)", placeholder="例: Y0001")
            
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

# ====== タブ5：ユーザー・ログ管理 ======
try:
    df_users = safe_read_worksheet(conn, "ユーザー", ["ユーザーID", "パスワード", "名前", "ステータス", "権限"])
    
    with tabs[5]:
        st.subheader("ユーザー承認・アクセスログ管理")
        
        st.markdown("#### ユーザーIDの承認待ち一覧")
        pending_users = df_users[df_users["ステータス"] == "未承認"]
        if pending_users.empty:
            st.write("現在、承認待ちのユーザーはいません。")
        else:
            for index, row in pending_users.iterrows():
                col_u1, col_u2 = st.columns([3, 1])
                with col_u1:
                    st.write(f"申請者: **{row['名前']}** (ID: {row['ユーザーID']})")
                with col_u2:
                    if st.button("承認する", key=f"approve_{row['ユーザーID']}"):
                        df_users.at[index, "ステータス"] = "OK"
                        conn.update(worksheet="ユーザー", data=df_users)
                        write_log(st.session_state.get("current_user_name", "管理者"), f"{row['名前']} のアカウントを承認")
                        st.success(f"{row['名前']} さんを承認しました。")
                        st.rerun()

        st.markdown("---")
        st.markdown("#### アクセス履歴（最新順）")
        if st.button("ログを更新"):
            st.cache_data.clear()
        
        try:
            df_logs = safe_read_worksheet(conn, "アクセスログ")
            if not df_logs.empty:
                display_dataframe(df_logs.iloc[::-1], use_container_width=True, hide_index=True)
            else:
                st.write("ログはまだありません。")
        except:
            st.write("ログシートがまだ作成されていません。")
            
except Exception as e:
    st.error(f"データ取得エラー: {e}")