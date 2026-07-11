"""スプレッドシート接続テスト（ローカル実行用）
使い方: python test_sheets_connection.py
"""
import re
import tomllib
from pathlib import Path

import gspread
from gspread.exceptions import APIError

SECRETS = Path(".streamlit/secrets.toml")

def normalize_id(raw):
    s = str(raw).strip().strip('"').strip("'")
    if "/spreadsheets/d/" in s:
        m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
        if m:
            return m.group(1)
    return s

def main():
    secrets = tomllib.loads(SECRETS.read_bytes())
    gs = secrets["connections"]["gsheets"]
    sid = normalize_id(gs["spreadsheet"])
    cfg = dict(gs["configuration"])
    email = cfg.get("client_email", "")
    pk = cfg.get("private_key", "")
    if "\\n" in pk:
        cfg["private_key"] = pk.replace("\\n", "\n")

    print("spreadsheet ID:", sid)
    print("service account:", email)

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        load_pem_private_key(cfg["private_key"].encode(), password=None)
        print("[OK] private_key")
    except Exception as e:
        print("[NG] private_key:", e)
        return

    try:
        client = gspread.service_account_from_dict(cfg)
        sh = client.open_by_key(sid)
        print("[OK] spreadsheet:", sh.title)
        print("[OK] worksheets:", [w.title for w in sh.worksheets()])
    except APIError as e:
        print(f"[NG] API {e.response.status_code}:", e.response.text[:300])
        if e.response.status_code == 404:
            print("\n→ スプレッドシート「共有」に次を「編集者」で追加:")
            print(" ", email)
    except Exception as e:
        print("[NG]", type(e).__name__, e)

if __name__ == "__main__":
    main()
