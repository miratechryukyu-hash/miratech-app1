import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, date
import qrcode
from io import BytesIO
import google.generativeai as genai
import json
import re
from PIL import Image
import base64
import time

# ==========================================
# 設定
# ==========================================
APP_URL = "https://miratech-app1-dzi7pmrrt5nzqt6be6swzn.streamlit.app/"

st.set_page_config(page_title="miratech 医療機器管理システム", layout="centered")

# 通信エラー対策：安全にスプレッドシートを読み込むためのリトライ関数
def safe_read_worksheet(conn, worksheet_name, default_columns=None):
    for i in range(3):
        try:
            # 💡 【修正】ttl=15 にして、Googleのアクセス制限(429エラー)を回避します
            df = conn.read(worksheet=worksheet_name, ttl=15)
            if df is not None:
                return df.dropna(how="all").fillna("")
        except Exception:
            if i < 2:
                time.sleep(1) # 1秒待って再試行
            else:
                st.error(f"スプレッドシート（{worksheet_name}）の読み込みに失敗しました。通信環境が良い場所で再度お試しください。")
    return pd.DataFrame(columns=default_columns) if default_columns else pd.DataFrame()

# データお掃除用の共通関数
def clean_data_str(val):
    s = str(val).replace("'", "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    if s.lower() == "nan":
        s = ""
    return s

def clean_series(series):
    return series.astype(str).str.replace("'", "", regex=False).str.replace(r'\.0$', '', regex=True).str.replace(r'^nan$', '', flags=re.IGNORECASE, regex=True).str.strip()

# ゼロ落ち防止用の関数
def protect_zeros(val_str):
    val_str = str(val_str).strip()
    if val_str.startswith("0") and val_str.isdigit():
        return f"'{val_str}"
    return val_str

# --- ログ書き込み用共通関数 ---
def write_log(user_name, action):
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
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
# ログイン認証
# ==========================================
def check_auth():
    if "logged_in_facility" not in st.session_state:
        st.session_state["logged_in_facility"] = None
    if "current_user_name" not in st.session_state:
        st.session_state["current_user_name"] = None

    if st.session_state["logged_in_facility"] is not None:
        return True

    st.warning("miratech 琉球 医療機器管理システム")
    tab1, tab2 = st.tabs(["ログイン", "新規利用申請"])

    with tab1:
        with st.form("login_form"):
            st.info("セキュリティ保護のため、ログインが必要です。")
            input_id = st.text_input("ユーザーID")
            input_pass = st.text_input("パスワード", type="password")
            
            if st.form_submit_button("ログイン", use_container_width=True):
                clean_id = input_id.strip()
                clean_pass = input_pass.strip()
                
                try:
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    df_users = safe_read_worksheet(conn, "ユーザー", ["ユーザーID", "パスワード", "名前", "ステータス", "権限"])
                    
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
                                
                                write_log(st.session_state["current_user_name"], "ログインしました")
                                st.rerun()
                                return True
                            else:
                                st.warning("現在、管理者の承認待ちです。許可が出るまでお待ちください。")
                        else:
                            st.error("パスワードが違います。")
                    else:
                        st.error("ユーザーIDが見つかりません。新規申請を行ってください。")
                except Exception as e:
                    st.error(f"データベース接続エラー: {e}")

    with tab2:
        st.write("初めて利用される方は、こちらから利用申請を行ってください。")
        with st.form("register_form"):
            st.caption("⚠️ **注意**: ユーザーIDとパスワードは **半角英数字のみ** で入力してください（漢字・ひらがな・カタカナ等は使用できません）。")
            
            new_id = st.text_input("希望するユーザーID", placeholder="例: user123")
            new_name = st.text_input("お名前（フルネーム）", placeholder="例: 安富 翔")
            new_pass = st.text_input("設定するパスワード", type="password", placeholder="例: pass456")
            
            if st.form_submit_button("利用申請を送信", use_container_width=True):
                if new_id and new_name and new_pass:
                    if not re.match(r'^[a-zA-Z0-9]+$', new_id) or not re.match(r'^[a-zA-Z0-9]+$', new_pass):
                        st.error("⚠️ エラー: ユーザーIDとパスワードに日本語や記号が含まれています。「半角英数字のみ」で入力してやり直してください。")
                    else:
                        try:
                            conn = st.connection("gsheets", type=GSheetsConnection)
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

# --- ログイン後の変数 ---
facility_name = st.session_state["logged_in_facility"]
url_me_no = st.query_params.get("me_no", "")
BASE_CATEGORIES = ["輸液ポンプ", "顕微鏡", "保育器", "分娩監視装置", "ネブライザー", "透視装置","無影灯","血圧計","超音波診断装置","超音波プローブ",
                   "ドプラ","検診台","血液ガス分析装置","吸引器類","加湿器類","分娩台","ベビーコット","哺乳瓶消毒器","煮沸消毒器","パルスオキシメーター",
                   "聴力検査器","光線治療器","酸素モニタ","電気メス","麻酔器","生体情報モニタ","手術台","子宮鏡","滅菌装置", "その他"]

# AI設定
ai_model = None
if "GEMINI_API_KEY" in st.secrets:
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        ai_model = genai.GenerativeModel('gemini-2.5-flash')
    except Exception as e:
        st.error(f"APIキーの設定エラー: {e}")

# 共通データベースの取得（購入業者プルダウン用）
conn = st.connection("gsheets", type=GSheetsConnection)
df_master_global = safe_read_worksheet(conn, "機器マスター")
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
        st.session_state["logged_in_facility"] = None
        st.session_state["current_user_name"] = None
        st.query_params.clear() 
        st.rerun()
        
    st.stop() 

# ==========================================
# 【ルートA】直接アクセスした場合（管理画面へ）
# ==========================================
st.sidebar.success(f"ログイン中: {st.session_state.get('current_user_name', '不明')}")
if st.sidebar.button("ログアウト"):
    write_log(st.session_state["current_user_name"], "ログアウトしました")
    st.session_state["logged_in_facility"] = None
    st.session_state["current_user_name"] = None
    st.rerun()

st.markdown(f"### {facility_name}")
st.title("医療機器点検・管理")

tab_names = ["点検入力", "マスター", "機器カルテ・実績", "QR発行", "新規機器登録", "ユーザー・ログ管理"]
tabs = st.tabs(tab_names)

# ====== タブ1：入力画面 ======
with tabs[0]:
    input_keyword = st.text_input("管理番号 または シリアルNo を入力して検索", placeholder="例: INP0001").strip()

    master_row = None
    if input_keyword and not df_master_global.empty:
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
        def_category = clean_data_str(master_row.get("カテゴリ", "その他"))
        full_meshun = clean_data_str(master_row.get("機種", ""))
        def_model = full_meshun.replace(f"{def_category}(", "").replace(")", "")
        scan_year_val = clean_data_str(master_row.get("製造年月日", ""))
        
        # 1年経過アラート
        last_check_str = clean_data_str(master_row.get("最終点検日", ""))
        if last_check_str:
            try:
                last_check_date = datetime.strptime(last_check_str, "%Y-%m-%d").date()
                days_passed = (date.today() - last_check_date).days
                if days_passed >= 365:
                    st.error(f" 警告: 最終点検から1年以上経過しています！（前回: {last_check_str} / 経過: {days_passed}日）")
                else:
                    st.info(f" 前回点検日: {last_check_str} (経過: {days_passed}日)")
            except:
                pass
        
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.text_input("管理番号", value=final_me_no, disabled=True)
            st.text_input("機器の種類", value=def_category, disabled=True)
        with col_m2:
            st.text_input("シリアルNo", value=final_sn, disabled=True)
            st.text_input("型式", value=def_model, disabled=True)

        device_category = def_category
        device_model = def_model
        is_registered = True
        
        # ==========================================
        # 【新機能】お預かりした点検表データに基づく基準値自動セット
        # ==========================================
        # デフォルト基準値
        min_flow, max_flow = 18.0, 22.0
        min_press, max_press = 30.0, 90.0
        flow_unit, press_unit = "ml", "kPa"
        test_condition_flow = "流量120ml/hr 10min (予定量20ml)"
        test_condition_press = "流量120ml/hr"

        # 型式ごとの個別判定（エクセルデータ準拠）
        if "TE-331" in device_model or "TE-351" in device_model or "TE-371" in device_model or "TE-381" in device_model:
            min_flow, max_flow = 19.4, 20.6
            min_press, max_press = 53.4, 80.0
        elif "AS-800" in device_model:
            min_flow, max_flow = 9.0, 11.0
            min_press, max_press = 0.0, 120.0  # 秒判定、または一般的なkpa範囲
            test_condition_flow = "流量60mL/h 10min (予定量10ml)"
        elif "OT-707" in device_model or "OT-818G" in device_model:
            min_flow, max_flow = 18.0, 22.0
            min_press, max_press = 30.0, 140.0
            test_condition_press = "流量25ml/h"
        elif "TE-LM830" in device_model:
            min_flow, max_flow = 18.0, 22.0
            min_press, max_press = 30.0, 120.0

        st.markdown("---")
        check_type = st.radio("点検区分", ["院内点検(miratech)", "メーカー点検", "メーカー修理・校正"], horizontal=True)
        
        if "last_check_date" not in st.session_state:
            st.session_state["last_check_date"] = date.today()

        with st.form("check_form"):
            check_date = st.date_input("作業日", value=st.session_state["last_check_date"])
            inspector = st.text_input("実施者", value=st.session_state.get("current_user_name", ""))
            
            # --- 簡易版：一括チェックフォーム ---
            if check_type == "院内点検(miratech)":
                st.write(f"### 📋 【{device_category}】 点検項目判定")
                
                c_chk1, c_chk2, c_chk3 = st.columns(3)
                with c_chk1:
                    status_exterior = st.radio("1. 外観点検（汚れ・破損・クランプ等）", ["OK", "NG", "該当なし"])
                with c_chk2:
                    status_alarm = st.radio("2. 各種警報（開始忘れ・気泡・ドア等）", ["OK", "NG", "該当なし"])
                with c_chk3:
                    status_operation = st.radio("3. 作動点検（機能・LED・時計等）", ["OK", "NG", "該当なし"])

                st.write("### 🔢 数値・精度測定（実測値）")
                col_num1, col_num2 = st.columns(2)
                with col_num1:
                    st.info(f"💡 基準値：{min_flow} 〜 {max_flow} {flow_unit} ({test_condition_flow})")
                    flow_acc = st.number_input(f"流量精度実測値 ({flow_unit})", value=float(max_flow+min_flow)/2, step=0.1)
                with col_num2:
                    st.info(f"💡 基準値：{min_press} 〜 {max_press} {press_unit} ({test_condition_press})")
                    occ_press = st.number_input(f"閉塞検出圧実測値 ({press_unit})", value=float(max_press+min_press)/2, step=1.0)
            else:
                st.info("外部対応（メーカー等）のため、数値測定はスキップされます。対応内容は備考欄に記入してください。")
                status_exterior = status_alarm = status_operation = "該当なし"
                flow_acc = occ_press = 0.0

            st.markdown("---")
            result = st.radio("総合評価", ["使用可", "メーカー修理", "廃棄"], horizontal=True) 
            memo = st.text_area("備考・報告欄（交換部品や報告書Noなど）", placeholder="特記事項があれば記入してください")
            
            submitted = st.form_submit_button("スプレッドシートに保存")

       if submitted:
            if not final_me_no:
                st.warning("管理番号が入力されていません。")
            else:
                # ==========================================
                # 【完全版】お預かりデータに基づく型式別基準値の自動セット
                # ==========================================
                min_flow, max_flow = 18.0, 22.0
                min_press, max_press = 30.0, 90.0
                flow_unit, press_unit = "ml", "kPa"
                test_condition_flow = "120ml/hr 10min (予定20ml)"
                test_condition_press = "120ml/hr M設定"

                # テルモ 輸液ポンプシリーズ
                if "TE-171" in device_model:
                    min_flow, max_flow = 19.0, 21.0
                    min_press, max_press = 6.0, 60.0
                    press_unit = "秒"
                    test_condition_press = "100ml/hr M(1.4m) 閉塞時間"
                elif "TE-161" in device_model or "TE-261" in device_model or "TE-281" in device_model:
                    min_flow, max_flow = 18.0, 22.0
                    min_press, max_press = 30.0, 90.0
                elif "TE-LM830" in device_model:
                    min_flow, max_flow = 18.0, 22.0
                    min_press, max_press = 30.0, 120.0

                # テルモ シリンジポンプシリーズ（TE-331, 351, 371, 381）
                elif "TE-331" in device_model or "TE-351" in device_model or "TE-371" in device_model or "TE-381" in device_model:
                    min_flow, max_flow = 19.4, 20.6
                    min_press, max_press = 53.4, 80.0
                    test_condition_press = "120ml/hr M設定(過負荷)"

                # JMS 輸液ポンプシリーズ（OT-707, 818G）
                elif "OT-707" in device_model or "OT-818G" in device_model:
                    min_flow, max_flow = 18.0, 22.0
                    min_press, max_press = 30.0, 140.0
                    test_condition_press = "25ml/hr 圧力計間1m"

                # アトム 輸液ポンプ（AS-800）
                elif "AS-800" in device_model:
                    min_flow, max_flow = 9.0, 11.0
                    min_press, max_press = 0.0, 2.0
                    press_unit = "分"
                    test_condition_flow = "60mL/h 10min (予定10ml)"
                    test_condition_press = "60mL/h 予定60mL(レベル5) 警報時間"

                # --- 安全装置：数値が基準値外なのに「使用可」で保存しようとしたらブロック ---
                has_error = False
                if check_type == "院内点検(miratech)" and result == "使用可":
                    if not (min_flow <= flow_acc <= max_flow):
                        st.error(f"アラーム：流量精度（{flow_acc} {flow_unit}）が基準値（{min_flow}〜{max_flow}）を外れています！【使用可】での保存はできません。")
                        has_error = True
                    if not (min_press <= occ_press <= max_press):
                        st.error(f"アラーム：閉塞圧/時間（{occ_press} {press_unit}）が基準値（{min_press}〜{max_press}）を外れています！【使用可】での保存はできません。")
                        has_error = True
                    if status_exterior == "NG" or status_alarm == "NG" or status_operation == "NG":
                        st.error("アラーム：一括判定に「NG」の項目があります！【使用可】での保存はできません。")
                        has_error = True

                if has_error:
                    st.error(" 基準値外の異常が検知されたため、データベースへの保存を強制中断しました。数値を再確認するか、評価を「メーカー修理」等に切り替えてください。")
                else:
                    try:
                        # 判定テキストの組み立て
                        flow_judge = "OK" if (min_flow <= flow_acc <= max_flow) else "NG"
                        press_judge = "OK" if (min_press <= occ_press <= max_press) else "NG"
                        
                        detail_text = f"【{check_type}】 外観:{status_exterior} / 警報:{status_alarm} / 作動:{status_operation} / 流量:{flow_acc}{flow_unit}({flow_judge}) / 閉塞:{occ_press}{press_unit}({press_judge})"

                        # ==========================================
                        # 印刷・履歴で完全共通化する本格HTML報告書レイアウト
                        # ==========================================
                        generated_html_report = f"""
<div style="font-family: sans-serif; font-size: 13px; color: black; background: white; padding: 25px; border: 2px solid #333; max-width: 750px; margin: 0 auto;">
<h2 style="text-align: center; border-bottom: 2px solid black; padding-bottom: 8px; margin-top:0;">医療機器 定期点検報告書</h2>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
<tr>
<td style="border: 1px solid black; padding: 6px; width: 20%; background: #f0f0f0;"><b>管理番号</b></td>
<td style="border: 1px solid black; padding: 6px; width: 30%;">{final_me_no}</td>
<td style="border: 1px solid black; padding: 6px; width: 20%; background: #f0f0f0;"><b>点検実施日</b></td>
<td style="border: 1px solid black; padding: 6px; width: 30%;">{check_date}</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; background: #f0f0f0;"><b>機種(型式)</b></td>
<td style="border: 1px solid black; padding: 6px;">{device_category}({device_model})</td>
<td style="border: 1px solid black; padding: 6px; background: #f0f0f0;"><b>点検実施者</b></td>
<td style="border: 1px solid black; padding: 6px;">{inspector}</td>
</tr>
</table>
<h4 style="margin-bottom:6px; border-left:4px solid #333; padding-left:8px;">■ 点検判定結果</h4>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 15px; text-align: center;">
<tr style="background: #f5f5f5; font-weight: bold;">
<td style="border: 1px solid black; padding: 6px; width: 40%;">点検区分</td>
<td style="border: 1px solid black; padding: 6px; width: 60%;">実施判定</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; text-align: left;">1. 外観・筐体点検（汚れ・破損・クランプ等）</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if status_exterior=='NG' else 'black'};">{status_exterior}</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; text-align: left;">2. 各種警報・アラーム試験（開始忘れ・気泡・ドア等）</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if status_alarm=='NG' else 'black'};">{status_alarm}</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; text-align: left;">3. 通電・基本作動点検（機能・LED・時計等）</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if status_operation=='NG' else 'black'};">{status_operation}</td>
</tr>
</table>
<h4 style="margin-bottom:6px; border-left:4px solid #333; padding-left:8px;">■ 測定精度実測値</h4>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 15px; text-align: center;">
<tr style="background: #f5f5f5; font-weight: bold;">
<td style="border: 1px solid black; padding: 6px; width: 25%;">測定項目</td>
<td style="border: 1px solid black; padding: 6px; width: 35%;">基準値（許容範囲）</td>
<td style="border: 1px solid black; padding: 6px; width: 25%;">実測値</td>
<td style="border: 1px solid black; padding: 6px; width: 15%;">判定</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; text-align: left;">流量精度</td>
<td style="border: 1px solid black; padding: 6px; font-size:11px;">{min_flow} 〜 {max_flow} {flow_unit}<br>({test_condition_flow})</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if flow_judge=='NG' else 'black'};">{flow_acc} {flow_unit}</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if flow_judge=='NG' else 'green'};">{flow_judge}</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 6px; text-align: left;">閉塞検出試験</td>
<td style="border: 1px solid black; padding: 6px; font-size:11px;">{min_press} 〜 {max_press} {press_unit}<br>({test_condition_press})</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if press_judge=='NG' else 'black'};">{occ_press} {press_unit}</td>
<td style="border: 1px solid black; padding: 6px; font-weight: bold; color: {'red' if press_judge=='NG' else 'green'};">{press_judge}</td>
</tr>
</table>
<table style="width: 100%; border-collapse: collapse;">
<tr>
<td style="border: 1px solid black; padding: 8px; width: 20%; background: #f0f0f0;"><b>総合評価</b></td>
<td style="border: 1px solid black; padding: 8px; font-size: 15px; font-weight: bold; color: {'green' if result=='使用可' else 'red'};">{result}</td>
</tr>
<tr>
<td style="border: 1px solid black; padding: 8px; background: #f0f0f0;"><b>備考・処置内容</b></td>
<td style="border: 1px solid black; padding: 8px; min-height: 45px; white-space: pre-wrap;">{memo if memo else "特記事項なし"}</td>
</tr>
</table>
<p style="text-align: right; font-size: 11px; color: gray; margin-top: 10px; margin-bottom: 0;">技術管理・保守責任: miratech 琉球</p>
</div>
"""

                        # --- データベースへの保存処理 ---
                        df_master = safe_read_worksheet(conn, "機器マスター")
                        mask = clean_series(df_master["管理番号"]) == clean_data_str(final_me_no)
                        
                        if mask.any():
                            df_master.loc[mask, "最終点検日"] = str(check_date)
                            df_master.loc[mask, "最終判定"] = f"{result}({check_type})"
                            df_master.loc[mask, "最終実施者"] = inspector
                            conn.update(worksheet="機器マスター", data=df_master)
                            
                            existing_history = safe_read_worksheet(conn, "点検履歴")
                            new_hist_row = pd.DataFrame([{
                                "点検日": str(check_date),
                                "管理番号": safe_final_me_no,
                                "カテゴリ": device_category,
                                "シリアルNo": safe_final_sn,
                                "製造年月日": scan_year_val,
                                "機種": f"{device_category}({device_model})",
                                "実施者": inspector,
                                "判定": result,
                                "詳細データ": detail_text,
                                "備考": memo,
                                "報告書HTML": generated_html_report
                            }])
                            updated_history = pd.concat([existing_history, new_hist_row], ignore_index=True)
                            conn.update(worksheet="点検履歴", data=updated_history)
                            
                            st.session_state["last_check_date"] = check_date
                            current_user = st.session_state.get("current_user_name", "不明")
                            write_log(current_user, f"{final_me_no} の点検データを保存({check_type} / 実施者: {inspector})")
                            
                            st.success(f" {final_me_no} の点検記録の保存、および台帳の更新が完了しました！")

                            # 画面に印刷用点検表とQRコードを表示
                            st.markdown("---")
                            st.subheader("🖨️ 提出用 点検報告書（印刷・PDF保存用）")
                            st.markdown(generated_html_report, unsafe_allow_html=True)
                            st.info("💡 このまま印刷・PDF化する場合はブラウザの印刷機能（Ctrl + P または Cmd + P）を実行してください。")
                            
                            st.markdown("---")
                            st.subheader(f"{final_me_no} 専用QRコード")
                            final_url = f"{APP_URL}/?me_no={final_me_no}"
                            qr = qrcode.QRCode(version=1, box_size=10, border=4)
                            qr.add_data(final_url)
                            qr.make(fit=True)
                            img = qr.make_image(fill_color="black", back_color="white")
                            buf = BytesIO()
                            img.save(buf, format="PNG")
                            byte_im = buf.getvalue()
                            b64 = base64.b64encode(byte_im).decode()
                            html_img = f'''
                            <a href="data:image/png;base64,{b64}" download="QR_{final_me_no}.png">
                                <img src="data:image/png;base64,{b64}" width="150" style="border: 2px solid #eee; padding: 10px; border-radius: 10px; background-color: white;">
                            </a>
                            '''
                            st.markdown(html_img, unsafe_allow_html=True)
                        else:
                            st.error("マスターにこの管理番号が存在しません。新規機器登録タブから登録してください。")
                    except Exception as e:
                        st.error(f"エラー: {e}")

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
                    st.dataframe(cat_counts, hide_index=True, use_container_width=True)
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
                st.dataframe(df, hide_index=True, use_container_width=True)
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
                        new_model = st.text_input("機種 (例: 輸液ポンプ(TE-131A))", value=clean_data_str(target_row.get("機種", "")))
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
                        new_delivery = st.date_input("納入日", value=saved_delivery_date, min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))

                        if st.form_submit_button("変更を上書き保存する", type="primary"):
                            safe_new_sn = protect_zeros(new_sn)

                            mask_m = master_me_nos == clean_edit_me_no
                            df_master_edit.loc[mask_m, "カテゴリ"] = new_cat
                            df_master_edit.loc[mask_m, "機種"] = new_model
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
                                        df_hist_edit.loc[mask_h, "機種"] = new_model
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
        st.markdown("#### 🛠️ 故障対応・修理完了の入力")
        st.write("現場から上がった故障報告に対して、修理対応と安全点検の結果を入力します。")

        try:
            df_failed = safe_read_worksheet(conn, "故障報告")
            
            if df_failed.empty:
                st.info("現在、故障報告データはありません。")
            else:
                # 「対応状況」が未対応のものだけを抽出
                df_pending = df_failed[df_failed["対応状況"].str.strip() == "未対応"]
                
                if df_pending.empty:
                    st.success("✅ 現在、対応待ちの故障報告はありません。すべての修理・点検が完了しています！")
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
                        
                        st.write("▼ 修理後の安全点検チェック（エビデンス確保）")
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
                        st.success(f"🎉 {target_me} の修理対応・安全点検の記録を保存し、台帳を更新しました！")
                        write_log(st.session_state.get("current_user_name", "ME"), f"{target_me} の故障対応・修理点検を完了")
                        
                        # 4. その場で即座に印刷・PDF化できる「修理・点検報告書」を画面に出現させる
                        st.markdown("---")
                        st.subheader("🖨️ 提出用 報告書の印刷レイアウト")
                        
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
                        st.info("💡 このまま紙に印刷またはPDF化する場合は、ブラウザの印刷機能（Ctrl + P 又は Cmd + P）を実行してください。自動的にキレイなA4報告書枠のみが印刷されます。")
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
            st.write("下の一覧表から、詳細を見たい機器の行をタップ（クリック）してください")
            if not df_master.empty:
                selection_event = st.dataframe(
                    df_master,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row"
                )
                
                if len(selection_event.selection.rows) > 0:
                    idx = selection_event.selection.rows[0]
                    # 💡 【修正】"ME No." ではなく "管理番号" を取得
                    target_me = clean_data_str(df_master.iloc[idx].get("管理番号", "不明"))
                    model_name = clean_data_str(df_master.iloc[idx].get("機種", "不明な機器"))
                    
                    st.markdown("---")
                    st.markdown(f"### {model_name} (管理番号: {target_me}) のカルテ")
                    
                    hist_df = pd.DataFrame()
                    if not df_history.empty and "管理番号" in df_history.columns:
                        clean_hist_search_me = clean_series(df_history["管理番号"])
                        hist_df = df_history[clean_hist_search_me == target_me].iloc[::-1]
                        
                    if not hist_df.empty:
                        st.write("#### 過去の点検・修理履歴")
                        st.dataframe(hist_df, use_container_width=True, hide_index=True)
                        
                        # 🖨️ 報告書の再発行（印刷）機能
                        st.markdown("---")
                        st.write("#### 🖨️ 報告書の再発行（印刷）")
                        st.write("履歴から特定の日の報告書をキレイなレイアウトで表示・印刷できます。")
                        
                        selected_date = st.selectbox("印刷したい点検日を選択してください", hist_df["点検日"].tolist())
                        
                        if selected_date:
                            report_data = hist_df[hist_df["点検日"] == selected_date].iloc[0]
                            
                            # 保存されているHTMLデザインを取り出す
                            saved_html = report_data.get("報告書HTML", "")
                            
                            if str(saved_html).strip() and str(saved_html).lower() != "nan":
                                # 新しいシステムで保存された場合は、本格レイアウトをそのまま表示
                                st.markdown(saved_html, unsafe_allow_html=True)
                                st.info("💡 上記の報告書をPDF化・紙に印刷するには、ブラウザの印刷機能（キーボードの `Ctrl + P` または `Cmd + P`）を使用してください。")
                            else:
                                # 古いデータ（HTMLが保存されていない時代）の場合は、簡易版を表示
                                html_report = f"""
                                <div style="padding: 30px; border: 2px solid #333; background-color: white; color: black; border-radius: 5px;">
                                    <h2 style="text-align: center; border-bottom: 2px solid black; padding-bottom: 10px;">医療機器 点検報告書 (旧フォーマット)</h2>
                                    <div style="text-align: right; margin-bottom: 20px;">点検日: {report_data.get('点検日', '-')}</div>
                                    <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
                                        <tr>
                                            <td style="padding: 10px; border: 1px solid #aaa; width: 30%; background-color: #f0f0f0;"><b>管理番号</b></td>
                                            <td style="padding: 10px; border: 1px solid #aaa;">{target_me}</td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>機種名</b></td>
                                            <td style="padding: 10px; border: 1px solid #aaa;">{model_name}</td>
                                        </tr>
                                        <tr>
                                            <td style="padding: 10px; border: 1px solid #aaa; background-color: #f0f0f0;"><b>詳細データ</b></td>
                                            <td style="padding: 10px; border: 1px solid #aaa;">{report_data.get('詳細データ', '-')}</td>
                                        </tr>
                                    </table>
                                </div>
                                """
                                st.markdown(html_report, unsafe_allow_html=True)
                                st.warning("※このデータは旧システムで保存されたため、詳細なレイアウトには対応していません。")
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
                    st.dataframe(daily_counts.iloc[::-1], use_container_width=True, hide_index=True)

                st.markdown("##### 特定の日の点検内訳を確認する")
                target_date = st.date_input("確認したい日付を選択", date.today())
                
                day_detail_df = df_history[df_history["点検日"] == str(target_date)]
                if not day_detail_df.empty:
                    st.success(f"{target_date} は 合計 {len(day_detail_df)} 台 の点検が完了しています。")
                    st.dataframe(day_detail_df, use_container_width=True, hide_index=True)
                else:
                    st.info(f"選択された日付（{target_date}）の点検データはありません。")
            else:
                st.info("集計できる点検履歴データがまだありません。")

    except Exception as e:
        st.error(f"システムエラー: {e}")

# ====== タブ4：QRコード発行機能 ======
with tabs[3]:
    st.subheader("機器用QRコードの作成")
    st.write("対象の「管理番号」を入力すると、機器に貼り付ける用のQRコードが作成されます。")
    
    target_qr_me = st.text_input("QRコードを作りたい「管理番号」を入力", placeholder="例: Y0001")
    
    if st.button("QRコードを作成する"):
        if target_qr_me:
            # URLの末尾の「/」を調整してキレイなリンクを作る
            clean_url = APP_URL.rstrip('/')
            final_url = f"{clean_url}/?me_no={target_qr_me}"
            
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(final_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            buf = BytesIO()
            img.save(buf, format="PNG")
            byte_im = buf.getvalue()
            
            st.success(f"「{target_qr_me}」専用のQRコードができました！")
            
            # 【追加】テプラ用にURLをテキスト表示（コピーボタン付き）
            st.write("▼ テプラ等にコピーして使うためのURL")
            st.code(final_url, language="text")
            
            b64 = base64.b64encode(byte_im).decode()
            html_img = f'''
            <a href="data:image/png;base64,{b64}" download="QR_{target_qr_me}.png">
                <img src="data:image/png;base64,{b64}" width="200" style="border: 2px solid #eee; padding: 10px; border-radius: 10px; background-color: white;">
            </a>
            <br>
            <p style="font-size: 14px; color: gray;">QRコードを<b>タップ（クリック）</b>すると直接ダウンロードされます。<br>スマホの場合は<b>長押しして「画像を保存」</b>も可能です。</p>
            '''
            st.markdown(html_img, unsafe_allow_html=True)
        else:
            st.warning("管理番号を入力してください。")

# ====== タブ5：新規機器の登録 ======
with tabs[4]:
    st.subheader("新規機器の直接登録")
    st.write("ここで登録した機器データは、直接「機器マスター」へ保存されます。点検は登録後に「点検入力」タブで行えます。")
    
    reg_mode = st.radio("入力方法を選択してください", ["AI銘板スキャナー", "手動で情報を入力"], horizontal=True)
    
    # 既存データから候補を自動生成
    df_m_reg = safe_read_worksheet(conn, "機器マスター")
    history_categories = sorted({clean_data_str(c) for c in df_m_reg["カテゴリ"].unique() if clean_data_str(c)})
    history_vendors = sorted({clean_data_str(v) for v in df_m_reg["購入業者"].unique() if clean_data_str(v)})

    if reg_mode == "AI銘板スキャナー":
        st.info("新しい機器の銘板を撮影すると、AIが情報を読み取ってくれます。")
        if ai_model is None:
            st.error("APIキーが設定されていないか、ライブラリのバージョンが古いです。")
        else:
            img_file = st.camera_input("銘板（シール）を撮影してください", key="ai_camera")
            
            if img_file:
                current_image_bytes = img_file.getvalue()
                if st.session_state.get("last_scanned_image") != current_image_bytes:
                    with st.spinner("AIが文字を解析しています（約10秒）..."):
                        try:
                            img = Image.open(img_file)
                            prompt = """
                            この医療機器の銘板写真から以下の情報を抜き出して、JSON形式で回答してください。
                            キーは以下のようにしてください:
                            - model (型式)
                            - serial_number (製造番号/SN)
                            - manufacture_year (製造年月日。例: 2018.10.10)
                            """
                            response = ai_model.generate_content([prompt, img])
                            json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
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
            man_me_no = st.text_input("①管理番号 (必須)", placeholder="例: Y0001")
            
            st.write("▼ ②機器種類（カテゴリ）※必須")
            sel_cat = st.selectbox(" 過去のリストから選ぶ", [""] + history_categories)
            txt_cat = st.text_input(" リストにない場合はここに直接入力", placeholder="例: 新しいポンプ")
            
            st.write("▼ ③購入業者")
            sel_vendor = st.selectbox("過去のリストから選ぶ", [""] + history_vendors)
            txt_vendor = st.text_input("リストにない場合はここに直接入力", placeholder="例: 〇〇医療器")
            
            st.markdown("---")
            man_maker = st.text_input("④メーカー", placeholder="例: テルモ")
            man_model = st.text_input("⑤型式 (機種)", value=st.session_state.get("scan_model", ""), placeholder="例: TE-131A")
            man_sn = st.text_input("⑥シリアルNo", value=st.session_state.get("scan_sn", ""), placeholder="例: 12345678")
            man_year = st.text_input("⑦製造年月日", value=st.session_state.get("scan_year", ""), placeholder="例: 2014")
            man_life = st.number_input("⑧耐用年数（年）", min_value=0, value=6, step=1)
            
            man_location = st.text_input("⑨設置場所", placeholder="例: 一般病棟")
            man_acq_type = st.selectbox("⑩導入形態", ["購入", "リース", "レンタル", "その他"])
            man_price = st.text_input("⑪購入金額", placeholder="例: 1500000")
            man_delivery = st.date_input("⑫納入日", value=date.today(), min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
            
            if st.form_submit_button("機器マスターに登録する", type="primary"):
                final_cat = txt_cat if txt_cat.strip() != "" else sel_cat
                final_vendor = txt_vendor if txt_vendor.strip() != "" else sel_vendor

                if not man_me_no or not final_cat:
                    st.error("管理番号 と 機器種類 は必須です！")
                else:
                    final_cat = clean_data_str(final_cat)
                    final_vendor = clean_data_str(final_vendor)
                    try:
                        # 💡 【修正】"ME No." ではなく "管理番号" を探すように変更
                        clean_db_me_reg = clean_series(df_m_reg["管理番号"])
                        
                        if clean_data_str(man_me_no) in clean_db_me_reg.values:
                            # 💡 【修正】エラーメッセージの "ME No." も "管理番号" に変更
                            st.error(f"{man_me_no} は既に登録されています。別の管理番号を指定してください。")
                        else:
                            new_master_row = pd.DataFrame([{
                                "管理番号": protect_zeros(man_me_no),
                                "カテゴリ": final_cat,
                                "メーカー": man_maker,
                                "機種": f"{final_cat}({man_model})",
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
                            st.success(f"{man_me_no} を登録しました！次回から「{final_cat}」も候補に表示されます。")
                            
                            st.session_state["scan_model"] = None 
                            st.session_state["scan_sn"] = None 
                            st.session_state["scan_year"] = None 
                            st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

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
                st.dataframe(df_logs.iloc[::-1], use_container_width=True, hide_index=True)
            else:
                st.write("ログはまだありません。")
        except:
            st.write("ログシートがまだ作成されていません。")
            
except Exception as e:
    st.error(f"データ取得エラー: {e}")
