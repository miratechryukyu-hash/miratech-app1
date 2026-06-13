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
APP_URL = "https://miratech-app-bwyle23rwce9hxkpvh3rk8.streamlit.app"

st.set_page_config(page_title="miratech 医療機器管理システム", layout="centered")

# 通信エラー対策：安全にスプレッドシートを読み込むためのリトライ関数
def safe_read_worksheet(conn, worksheet_name, default_columns=None):
    for i in range(3):
        try:
            df = conn.read(worksheet=worksheet_name, ttl=0)
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
            new_id = st.text_input("希望するユーザーID")
            new_name = st.text_input("お名前（フルネーム）")
            new_pass = st.text_input("設定するパスワード", type="password")
            
            if st.form_submit_button("利用申請を送信", use_container_width=True):
                if new_id and new_name and new_pass:
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
categories_list = ["輸液ポンプ", "顕微鏡", "保育器", "分娩監視装置", "ネブライザー", "透視装置","無影灯","血圧計","超音波診断装置",
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
existing_vendors = []
if not df_master_global.empty and "購入業者" in df_master_global.columns:
    # 登録済みの業者を抽出し、空白を除外
    existing_vendors = [v for v in df_master_global["購入業者"].unique() if str(v).strip() != "" and str(v).lower() != "nan"]
vendor_options = existing_vendors + ["新規追加(手入力)"]

# ==========================================
# 【ルートB】QRコードを読み取った場合（トラブル報告画面へ直行）
# ==========================================
if url_me_no:
    st.markdown(f"<h2 style='text-align: center; color: #FF4B4B;'>{facility_name}</h2>", unsafe_allow_html=True)
    st.markdown("<h3 style='text-align: center;'>機器トラブル報告システム</h3>", unsafe_allow_html=True)
    
    st.success(f"対象機器: {url_me_no}")
    
    with st.form("nurse_report_form"):
        rep_date = st.date_input("発生日", value=date.today(), min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
        rep_dept = st.selectbox("あなたの部署", ["選択してください", "外来", "一般病棟", "療養病棟", "オペ室", "透析室", "その他"])
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
                existing_data = safe_read_worksheet(conn, "故障報告", ["報告日", "発生日", "ME No.", "機種", "報告者", "部署", "症状", "対応状況"])
                
                new_report = pd.DataFrame([{
                    "報告日": str(date.today()),
                    "発生日": str(rep_date),
                    "ME No.": url_me_no,
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
    input_keyword = st.text_input("ME No. または 製造番号(S/N) を入力して検索", placeholder="例: Y0001 または 12345678").strip()

    master_row = None
    if input_keyword and not df_master_global.empty:
        clean_keyword = clean_data_str(input_keyword)
        clean_db_me = clean_series(df_master_global["ME No."])
        clean_db_sn = clean_series(df_master_global["製造番号"])
        
        matched_me = df_master_global[clean_db_me == clean_keyword]
        if not matched_me.empty:
            master_row = matched_me.iloc[0]
        else:
            matched_sn = df_master_global[clean_db_sn == clean_keyword]
            if not matched_sn.empty:
                master_row = matched_sn.iloc[0]

    incubator_type = "閉鎖式" 

    if master_row is not None:
        st.success("登録済みの機器が見つかりました。情報を自動出現させます。")
        final_me_no = clean_data_str(master_row.get("ME No.", ""))
        final_sn = clean_data_str(master_row.get("製造番号", ""))
        def_category = clean_data_str(master_row.get("カテゴリ", "その他"))
        full_meshun = clean_data_str(master_row.get("機種", ""))
        def_model = full_meshun.replace(f"{def_category}(", "").replace(")", "")
        scan_year_val = clean_data_str(master_row.get("製造年", ""))
        
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.text_input("ME No.", value=final_me_no, disabled=True)
            st.text_input("機器の種類", value=def_category, disabled=True)
        with col_m2:
            st.text_input("製造番号 (S/N)", value=final_sn, disabled=True)
            st.text_input("型式", value=def_model, disabled=True)

        device_category = def_category
        device_model = def_model
        is_registered = True
        
        if device_category == "保育器":
            incubator_type = st.radio("保育器のタイプ（点検リスト切り替え用）", ["閉鎖式", "開放型"])

    else:
        if input_keyword:
            st.info("該当する機器が見つかりません。新規登録が必要な場合は「新規機器登録」タブから登録してください。")
            st.stop() 

    if master_row is not None:
        st.markdown("---")
        check_type = st.radio("点検区分", ["院内・ME点検", "メーカー点検", "メーカー修理・校正", "その他外部委託"], horizontal=True)
        
        if "last_check_date" not in st.session_state:
            st.session_state["last_check_date"] = date.today()

        with st.form("check_form"):
            col_form1, col_form2 = st.columns(2)
            with col_form1: 
                check_date = st.date_input("作業日", value=st.session_state["last_check_date"], min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
            with col_form2: 
                st.text_input("対象機器 (確認用)", value=f"ME No: {final_me_no} / SN: {final_sn}" if is_registered or input_keyword else "", disabled=True)
            
            chk_e1=chk_e2=chk_e3=chk_e4=chk_e5=chk_e6=chk_e7 = False
            chk_a1=chk_a2=chk_a3=chk_a4 = False
            chk_op1=chk_op2=chk_op3 = False
            chk_es1=chk_es2=chk_es3=chk_es4=chk_es5=chk_es6 = False
            chk_as1=chk_as2=chk_as3=chk_as4=chk_as5 = False
            chk_sop1=chk_sop2=chk_sop3 = False 
            flow_acc=occ_press = 0.0
            bubble_ad_water=bubble_ad_nowater = 0
            inc_c_checks = {}
            inc_o_checks = {}
            inc_temp_disp = inc_temp_meas = 36.0
            exterior_result = "異常なし"
            detail_result = ""

            if check_type == "院内・ME点検":
                st.write(f"### 【{device_category} : {device_model}】専用チェック")
                
                if device_category == "輸液ポンプ":
                    with st.expander("① 外観・作動・警報の詳細チェック", expanded=True):
                        st.write("**【外観・作動点検】**")
                        col1, col2 = st.columns(2)
                        with col1:
                            chk_e1 = st.checkbox("本体の汚れ・破損なし", value=True)
                            chk_e2 = st.checkbox("ポールクランプ用ネジ穴", value=True)
                            chk_e3 = st.checkbox("チューブクランプ動作", value=True)
                            chk_e4 = st.checkbox("フィンガー部動作", value=True)
                        with col2:
                            chk_e5 = st.checkbox("AC・DC切り替え", value=True)
                            chk_e6 = st.checkbox("セルフチェック機能", value=True)
                            chk_e7 = st.checkbox("表示部LED", value=True)
                        
                        st.write("**【その他の作動点検】**")
                        col5, col6 = st.columns(2)
                        with col5:
                            chk_op1 = st.checkbox("積算クリア機能", value=True)
                            chk_op2 = st.checkbox("流量設定", value=True)
                        with col6:
                            chk_op3 = st.checkbox("日付・時刻設定", value=True)

                        st.write("**【各種警報点検】**")
                        col3, col4 = st.columns(2)
                        with col3:
                            chk_a1 = st.checkbox("開始忘れ / 流量設定無し", value=True)
                            chk_a2 = st.checkbox("気泡検出 / ドアオープン", value=True)
                        with col4:
                            chk_a3 = st.checkbox("輸液完了 / 再警報", value=True)
                            chk_a4 = st.checkbox("消音機能", value=True)
                    
                    st.write("**② 数値・精度チェック**")
                    col_num1, col_num2 = st.columns(2)
                    with col_num1:
                        flow_acc = st.number_input("流量精度 (ml)", value=20.0, step=0.1)
                        bubble_ad_water = st.number_input("気泡センサーAD値 (水入り)", value=120)
                    with col_num2:
                        occ_press = st.number_input("閉塞検出圧 (kpa/mmHg)", value=50.0, step=1.0)
                        bubble_ad_nowater = st.number_input("気泡センサーAD値 (水無し)", value=5)

                elif device_category == "シリンジポンプ":
                    with st.expander("① 外観・作動・警報の詳細チェック", expanded=True):
                        st.write("**【外観・作動点検】**")
                        col1, col2 = st.columns(2)
                        with col1:
                            chk_es1 = st.checkbox("本体の汚れ・破損なし", value=True)
                            chk_es2 = st.checkbox("ポールクランプ用ネジ穴", value=True)
                            chk_es3 = st.checkbox("シリンジクランプ動作", value=True)
                        with col2:
                            chk_es4 = st.checkbox("スライダー・クラッチ動作", value=True)
                            chk_es5 = st.checkbox("AC・DC切り替え", value=True)
                            chk_es6 = st.checkbox("セルフチェック・LED", value=True)
                        
                        st.write("**【その他の作動点検】**")
                        col7, col8 = st.columns(2)
                        with col7:
                            chk_sop1 = st.checkbox("積算クリア機能", value=True)
                            chk_sop2 = st.checkbox("流量設定", value=True)
                        with col8:
                            chk_sop3 = st.checkbox("日付・時刻設定", value=True)

                        st.write("**【各種警報点検】**")
                        col3, col4 = st.columns(2)
                        with col3:
                            chk_as1 = st.checkbox("シリンジ外れ・サイズ認識", value=True)
                            chk_as2 = st.checkbox("押し子外れ / クラッチ外れ", value=True)
                            chk_as3 = st.checkbox("残量 / 閉塞警報", value=True)
                        with col4:
                            chk_as4 = st.checkbox("開始忘れ / 流量設定無し", value=True)
                            chk_as5 = st.checkbox("消音 / 再警報", value=True)
                    
                    st.write("**② 数値・精度チェック**")
                    col_num1_s, col_num2_s = st.columns(2)
                    with col_num1_s:
                        flow_acc = st.number_input("流量精度チェック (ml)", value=10.0, step=0.1)
                    with col_num2_s:
                        occ_press = st.number_input("閉塞検出圧 (kpa)", value=80.0, step=1.0)

                elif device_category == "保育器":
                    if "閉鎖式" in incubator_type:
                        with st.expander("閉鎖式保育器 点検項目", expanded=True):
                            st.write("**① 外観点検**")
                            c1, c2 = st.columns(2)
                            with c1:
                                inc_c_checks["本体・フード破損なし"] = st.checkbox("本体・パネル・フード等に破損なし", value=True)
                                inc_c_checks["キャスター動作"] = st.checkbox("キャスター・ストッパー動作", value=True)
                                inc_c_checks["手入れ窓パッキン"] = st.checkbox("手入れ窓・パッキン破損なし", value=True)
                                inc_c_checks["ホース破損なし"] = st.checkbox("ホースアッセンブリ破損なし", value=True)
                            with c2:
                                inc_c_checks["フィルター状態"] = st.checkbox("フィルター汚れなし・期限内", value=True)
                                inc_c_checks["電源コード・プラグ"] = st.checkbox("電源・プラグ・アースピン破損なし", value=True)
                                inc_c_checks["センサー破損なし"] = st.checkbox("各種センサー・接続部破損なし", value=True)

                            st.write("**② 作動・機能点検**")
                            c3, c4 = st.columns(2)
                            with c3:
                                inc_c_checks["傾斜装置"] = st.checkbox("傾斜装置スムーズ動作", value=True)
                                inc_c_checks["ファン作動"] = st.checkbox("ファン確実作動・破損なし", value=True)
                            with c4:
                                inc_c_checks["加湿警報"] = st.checkbox("低水位・水槽外れ警報作動", value=True)
                                inc_c_checks["SpO2表示"] = st.checkbox("SpO2表示・測定(対応機のみ)", value=True)

                            st.write("**③ 温度制御 (設定 36.0±1℃)**")
                            c5, c6 = st.columns(2)
                            with c5:
                                inc_temp_disp = st.number_input("表示値 (℃)", value=36.0, step=0.1)
                            with c6:
                                inc_temp_meas = st.number_input("測定値 (℃)", value=36.0, step=0.1)
                    else:
                        with st.expander("開放型保育器 点検項目", expanded=True):
                            st.write("**① コントロール・作動・表示点検**")
                            o1, o2 = st.columns(2)
                            with o1:
                                inc_o_checks["電源・照明スイッチ"] = st.checkbox("電源・照明灯スイッチ異常なし", value=True)
                                inc_o_checks["表示・キー操作"] = st.checkbox("表示部・キー操作異常なし", value=True)
                                inc_o_checks["温度制御(マニュアル)"] = st.checkbox("マニュアルコントロール動作", value=True)
                                inc_o_checks["温度制御(サーボ)"] = st.checkbox("体温プローブ・サーボ動作", value=True)
                            with o2:
                                inc_o_checks["SpO2表示"] = st.checkbox("SpO2・HR表示測定が可能か", value=True)
                                inc_o_checks["タイマー表示"] = st.checkbox("タイマー機能・表示動作", value=True)

                            st.write("**② 各種警報機能**")
                            o3, o4 = st.columns(2)
                            with o3:
                                inc_o_checks["チェックスイッチ"] = st.checkbox("チェックスイッチ作動", value=True)
                                inc_o_checks["設定温度警報(マニュアル)"] = st.checkbox("設定温度警報(マニュアル)", value=True)
                                inc_o_checks["設定温度警報(皮膚温)"] = st.checkbox("設定温度警報(皮膚温)", value=True)
                            with o4:
                                inc_o_checks["プローブ警報"] = st.checkbox("プローブ警報作動", value=True)
                                inc_o_checks["停電警報"] = st.checkbox("停電警報作動", value=True)
                                inc_o_checks["キャノピ傾斜"] = st.checkbox("キャノピ傾斜動作", value=True)

                            st.write("**③ 蘇生装置・酸素・外装**")
                            o5, o6 = st.columns(2)
                            with o5:
                                inc_o_checks["蘇生装置"] = st.checkbox("蘇生装置の機能点検・異常なし", value=True)
                                inc_o_checks["酸素ブレンダ作動"] = st.checkbox("酸素ブレンダ作動確認", value=True)
                                inc_o_checks["供給ガス警報"] = st.checkbox("供給ガスが発生するか", value=True)
                            with o6:
                                inc_o_checks["吸引・流量計"] = st.checkbox("吸引ユニット・酸素流量計正常", value=True)
                                inc_o_checks["外装・キャノピ・ネジ類"] = st.checkbox("支柱・キャノピ・反射板・ネジ等", value=True)
                                inc_o_checks["電源・ジャック・ガード"] = st.checkbox("電源コード・各種ジャック・ガード", value=True)

                else:
                    exterior_result = st.radio("外装点検", ["異常なし", "異常あり"], horizontal=True)
                    detail_result = st.text_input("精度チェック（測定値など）", placeholder="例: 換気量 500ml")
            else:
                st.info("メーカーや外部業者の対応です。細かいチェック入力は省略されます。一番下の「備考・報告欄」に対応内容や報告書No.を記載してください。")

            st.markdown("---")
            
            inspector_label = "実施者（自社名、またはメーカー・業者名）" if check_type != "院内・ME点検" else "実施者"
            inspector = st.text_input(inspector_label, value=st.session_state.get("current_user_name", ""))
            result = st.radio("総合評価", ["使用可", "メーカー修理", "廃棄"], horizontal=True) 
            memo = st.text_area("備考・報告欄", placeholder="メーカーの作業報告書No.や、交換部品、対応内容などを記載してください")
            
            submitted = st.form_submit_button("スプレッドシートに保存")

        if submitted:
            if not final_me_no:
                st.warning("ME No. が入力されていません。")
            else:
                try:
                    existing_data = safe_read_worksheet(conn, "点検履歴")
                    
                    details_list = [f"【{check_type}】"]
                    
                    if check_type == "院内・ME点検":
                        if device_category == "輸液ポンプ":
                            details_list.append(f"汚れ破損:{'〇' if chk_e1 else '×'}, クランプ動作:{'〇' if chk_e3 else '×'}, 流量精度:{flow_acc}ml, 閉塞圧:{occ_press}kpa")
                        elif device_category == "シリンジポンプ":
                            details_list.append(f"汚れ破損:{'〇' if chk_es1 else '×'}, クランプ動作:{'〇' if chk_es3 else '×'}, 流量精度:{flow_acc}ml, 閉塞圧:{occ_press}kpa")
                        elif device_category == "保育器":
                            if "閉鎖式" in incubator_type:
                                c_chk_str = ", ".join([f"{k}:{'〇' if v else '×'}" for k, v in inc_c_checks.items()])
                                details_list.append(f"閉鎖式 [{c_chk_str}, 表示温度:{inc_temp_disp}℃, 測定温度:{inc_temp_meas}℃]")
                            else:
                                o_chk_str = ", ".join([f"{k}:{'〇' if v else '×'}" for k, v in inc_o_checks.items()])
                                details_list.append(f"開放型 [{o_chk_str}]")
                        else:
                            details_list.append(f"外装:{exterior_result}, 精度:{detail_result}")
                    else:
                        details_list.append("詳細は備考欄またはメーカー報告書を参照")
                    
                    detail_text = " / ".join(details_list)

                    safe_final_me_no = protect_zeros(final_me_no)
                    safe_final_sn = protect_zeros(final_sn)

                    data_dict = {
                        "点検日": str(check_date), 
                        "ME No.": safe_final_me_no, 
                        "カテゴリ": device_category,
                        "製造番号": safe_final_sn, 
                        "製造年": scan_year_val, 
                        "機種": f"{device_category}({device_model})", 
                        "実施者": inspector, 
                        "判定": result, 
                        "詳細データ": detail_text,
                        "備考": memo
                    }

                    new_data = pd.DataFrame([data_dict])
                    updated_df = pd.concat([existing_data, new_data], ignore_index=True)
                    conn.update(worksheet="点検履歴", data=updated_df)
                    
                    master_df = safe_read_worksheet(conn, "機器マスター")

                    existing_location = ""
                    existing_vendor = ""
                    existing_delivery = ""
                    existing_acq_type = ""
                    existing_price = ""

                    if master_row is not None:
                        existing_location = clean_data_str(master_row.get("設置場所", ""))
                        existing_vendor = clean_data_str(master_row.get("購入業者", ""))
                        existing_delivery = clean_data_str(master_row.get("納入日", ""))
                        existing_acq_type = clean_data_str(master_row.get("導入形態", ""))
                        existing_price = clean_data_str(master_row.get("購入金額", ""))

                    new_master_entry = pd.DataFrame([{
                        "ME No.": safe_final_me_no,
                        "カテゴリ": device_category,
                        "機種": f"{device_category}({device_model})",
                        "製造番号": safe_final_sn,
                        "製造年": scan_year_val,
                        "設置場所": existing_location,
                        "購入業者": existing_vendor,
                        "導入形態": existing_acq_type,
                        "購入金額": existing_price,
                        "納入日": existing_delivery,
                        "最終点検日": str(check_date),
                        "最終判定": f"{result}({check_type})",
                        "最終実施者": inspector
                    }])

                    if not master_df.empty and "ME No." in master_df.columns:
                        clean_master_df_me = clean_series(master_df["ME No."])
                        master_df = master_df[clean_master_df_me != clean_data_str(final_me_no)]
                    
                    updated_master_df = pd.concat([master_df, new_master_entry], ignore_index=True)
                    conn.update(worksheet="機器マスター", data=updated_master_df)
                    
                    st.session_state["last_check_date"] = check_date
                    
                    write_log(inspector, f"{final_me_no} の点検データを保存({check_type})")
                    
                    st.success(f"{final_me_no} の点検記録と、機器マスター台帳の更新が完了しました！")

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
                    <br>
                    <p style="font-size: 14px; color: gray;">QRコードを<b>タップ（クリック）</b>すると直接ダウンロードされます。<br>スマホの場合は<b>長押しして「画像を保存」</b>も可能です。</p>
                    '''
                    st.markdown(html_img, unsafe_allow_html=True)

                except Exception as e:
                    st.error(f"エラー: {e}")

# ====== タブ2：マスター ======
with tabs[1]:
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
        st.write("ME No.を入力すると現在のデータが呼び出され、内容を上書き修正できます。")

        edit_me_no = st.text_input("修正したい機器の「ME No.」を入力", placeholder="例: Y0001", key="edit_me_input").strip()

        if edit_me_no:
            try:
                df_master_edit = safe_read_worksheet(conn, "機器マスター")

                clean_edit_me_no = clean_data_str(edit_me_no)
                master_me_nos = clean_series(df_master_edit["ME No."])

                if not df_master_edit.empty and clean_edit_me_no in master_me_nos.values:
                    target_row = df_master_edit[master_me_nos == clean_edit_me_no].iloc[0]

                    with st.form("edit_master_form"):
                        st.info(f"{clean_edit_me_no} のデータを修正します。直したい箇所を書き換えて「保存」を押してください。")
                        
                        new_cat = st.text_input("カテゴリ", value=clean_data_str(target_row.get("カテゴリ", "")))
                        new_model = st.text_input("機種 (例: 輸液ポンプ(TE-131A))", value=clean_data_str(target_row.get("機種", "")))
                        new_sn = st.text_input("製造番号 (S/N)", value=clean_data_str(target_row.get("製造番号", "")))
                        new_year = st.text_input("製造年", value=clean_data_str(target_row.get("製造年", "")))
                        
                        new_location = st.text_input("設置場所", value=clean_data_str(target_row.get("設置場所", "")))
                        
                        # 業者をプルダウン + 手入力対応
                        saved_vendor = clean_data_str(target_row.get("購入業者", ""))
                        if saved_vendor and saved_vendor not in vendor_options:
                            vendor_options.insert(0, saved_vendor)
                        
                        sel_idx = vendor_options.index(saved_vendor) if saved_vendor in vendor_options else 0
                        edit_vendor_sel = st.selectbox("購入業者", vendor_options, index=sel_idx)
                        if edit_vendor_sel == "新規追加(手入力)":
                            new_vendor = st.text_input("購入業者を新規入力")
                        else:
                            new_vendor = edit_vendor_sel

                        # 導入形態と購入金額の追加
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
                            df_master_edit.loc[mask_m, "製造番号"] = safe_new_sn
                            df_master_edit.loc[mask_m, "製造年"] = new_year
                            df_master_edit.loc[mask_m, "設置場所"] = new_location
                            df_master_edit.loc[mask_m, "購入業者"] = new_vendor
                            df_master_edit.loc[mask_m, "導入形態"] = new_acq_type
                            df_master_edit.loc[mask_m, "購入金額"] = new_price
                            df_master_edit.loc[mask_m, "納入日"] = str(new_delivery)
                            conn.update(worksheet="機器マスター", data=df_master_edit)

                            try:
                                df_hist_edit = safe_read_worksheet(conn, "点検履歴")
                                if not df_hist_edit.empty and "ME No." in df_hist_edit.columns:
                                    clean_hist_me = clean_series(df_hist_edit["ME No."])
                                    mask_h = clean_hist_me == clean_edit_me_no
                                    if mask_h.any():
                                        df_hist_edit.loc[mask_h, "カテゴリ"] = new_cat
                                        df_hist_edit.loc[mask_h, "機種"] = new_model
                                        df_hist_edit.loc[mask_h, "製造番号"] = safe_new_sn
                                        df_hist_edit.loc[mask_h, "製造年"] = new_year
                                        conn.update(worksheet="点検履歴", data=df_hist_edit)
                            except Exception:
                                pass 
                            
                            st.cache_data.clear() 
                            st.success(f"{clean_edit_me_no} のデータを最新に修正し、過去の履歴にも完全に同期しました！")
                            write_log(st.session_state.get("current_user_name", "管理者"), f"{clean_edit_me_no} のデータを修正・同期")
                else:
                    st.warning("指定された ME No. は登録されていません。")
            except Exception as e:
                st.error(f"データ取得エラー: {e}")

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
                    target_me = clean_data_str(df_master.iloc[idx].get("ME No.", ""))
                    model_name = clean_data_str(df_master.iloc[idx].get("機種", "不明な機器"))
                    
                    st.markdown("---")
                    st.markdown(f"### {model_name} (ME No: {target_me}) のカルテ")
                    
                    if not df_history.empty and "ME No." in df_history.columns:
                        clean_hist_search_me = clean_series(df_history["ME No."])
                        hist_df = df_history[clean_hist_search_me == target_me].iloc[::-1]
                        
                        if not hist_df.empty:
                            st.write("#### 過去の点検・修理履歴")
                            st.dataframe(hist_df, use_container_width=True, hide_index=True)
                            
                            last_date = clean_data_str(hist_df.iloc[0].get("点検日", "-"))
                            last_result = clean_data_str(hist_df.iloc[0].get("判定", "-"))
                            st.success(f"最新の点検日: {last_date} ／ 判定: {last_result}")
                        else:
                            st.info("この機器の点検・修理履歴はありません。")
                    else:
                        st.info("点検履歴データがありません。")
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
    st.write("対象の「ME No.」を入力すると、機器に貼り付ける用のQRコードが作成されます。")
    
    target_qr_me = st.text_input("QRコードを作りたい「ME No.」を入力", placeholder="例: Y0001")
    
    if st.button("QRコードを作成する"):
        if target_qr_me:
            final_url = f"{APP_URL}/?me_no={target_qr_me}"
            
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(final_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            buf = BytesIO()
            img.save(buf, format="PNG")
            byte_im = buf.getvalue()
            
            st.success(f"「{target_qr_me}」専用のQRコードができました！")
            
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
            st.warning("ME No.を入力してください。")

# ====== タブ5：新規機器の登録 ======
with tabs[4]:
    st.subheader("新規機器の直接登録")
    st.write("ここで登録した機器データは、直接「機器マスター」へ保存されます。点検は登録後に「点検入力」タブで行えます。")
    
    reg_mode = st.radio("入力方法を選択してください", ["AI銘板スキャナー", "手動で情報を入力"], horizontal=True)
    
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
                            - manufacture_year (製造年。例: 2018)
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
            man_me_no = st.text_input("ME No. (必須)", placeholder="例: Y0001")
            
            cat_selection = st.selectbox("機器種類 (カテゴリ)", categories_list + ["その他(手入力)"])
            if cat_selection == "その他(手入力)":
                man_cat = st.text_input("機器種類を入力してください", placeholder="例: 保育器")
            else:
                man_cat = cat_selection
                
            man_model = st.text_input("型式 (機種)", value=st.session_state.get("scan_model", ""), placeholder="例: TE-131A")
            man_sn = st.text_input("製造番号 (S/N)", value=st.session_state.get("scan_sn", ""), placeholder="例: 12345678")
            man_year = st.text_input("製造年", value=st.session_state.get("scan_year", ""), placeholder="例: 2014-06-12")
            man_location = st.text_input("設置場所", placeholder="例: 一般病棟")
            
            # 購入業者のプルダウン + 手入力対応
            vendor_selection = st.selectbox("購入業者", vendor_options)
            if vendor_selection == "新規追加(手入力)":
                man_vendor = st.text_input("購入業者を入力してください", placeholder="例: 〇〇医療器")
            else:
                man_vendor = vendor_selection
                
            # 導入形態と購入金額の追加
            man_acq_type = st.selectbox("導入形態", ["購入", "リース", "レンタル", "その他"])
            man_price = st.text_input("購入金額(円)", placeholder="例: 1500000")

            man_delivery = st.date_input("納入日", value=date.today(), min_value=date(1950, 1, 1), max_value=date(2100, 12, 31))
            
            if st.form_submit_button("機器マスターに登録する", type="primary"):
                if not man_me_no or not man_cat:
                    st.error("ME No. と 機器種類 は必須です！")
                else:
                    try:
                        df_master_reg = safe_read_worksheet(conn, "機器マスター")
                        clean_db_me_reg = clean_series(df_master_reg["ME No."])
                        
                        if clean_data_str(man_me_no) in clean_db_me_reg.values:
                            st.error(f"{man_me_no} は既に登録されています。別のME No.を指定してください。")
                        else:
                            new_master_row = pd.DataFrame([{
                                "ME No.": protect_zeros(man_me_no),
                                "カテゴリ": man_cat,
                                "機種": f"{man_cat}({man_model})",
                                "製造番号": protect_zeros(man_sn),
                                "製造年": man_year,
                                "設置場所": man_location,
                                "購入業者": man_vendor,
                                "導入形態": man_acq_type,
                                "購入金額": man_price,
                                "納入日": str(man_delivery),
                                "最終点検日": "",
                                "最終判定": "",
                                "最終実施者": ""
                            }])
                            updated_master_reg = pd.concat([df_master_reg, new_master_row], ignore_index=True)
                            conn.update(worksheet="機器マスター", data=updated_master_reg)
                            
                            write_log(st.session_state.get("current_user_name", "管理者"), f"{man_me_no} を新規登録")
                            st.success(f"{man_me_no} を機器マスターに登録しました！「点検入力」タブから検索して点検を行えます。")
                            
                            st.session_state["scan_model"] = None 
                            st.session_state["scan_sn"] = None 
                            st.session_state["scan_year"] = None 
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

# ====== 追加：タブ6：ユーザー・ログ管理 ======
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