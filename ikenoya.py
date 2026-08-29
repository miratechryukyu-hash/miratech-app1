import time
import streamlit as st

# ページの基本設定
st.set_page_config(
    page_title="臨床工学技士 国家試験 模試アプリ", page_icon="📝"
)

# セッション状態（データ保存用）の初期化
if "score" not in st.session_state:
  st.session_state.score = 0
if "submitted" not in st.session_state:
  st.session_state.submitted = False

st.title("🩺 臨床工学技士 国家試験 模擬試験")
st.write(
    "本番の形式を意識した○×クイズや数値入力の練習ができるオリジナルアプリです。"
)

# タイマー機能のシミュレーション（例：3時間＝10800秒）
# ここでは簡易的に残り時間を表示
st.sidebar.header("⏱️ 試験ステータス")
st.sidebar.markdown("**制限時間:** 3時間 00分")
st.sidebar.markdown("**現在のモード:** 演習モード")

# 問題データ（本来はJSONや外部ファイルから読み込む）
questions = [
    {
        "id": 1,
        "category": "医用電気電子工学",
        "type": "choice",
        "question": (
            "直流回路において、抵抗に流れる電流は電圧に比例し、抵抗値に反比例する。"
        ),
        "options": ["○", "×"],
        "answer": "○",
        "explanation": (
            "オームの法則（I = V / R）に関する基本的な問題です。電流は電圧に比例し、抵抗に反比例します。"
        ),
    },
    {
        "id": 2,
        "category": "生体物性材料工学",
        "type": "numeric",
        "question": (
            "健康な成人の安静時における1回換気量の一般的な目安はおよそ何mLか？"
            "（数値を入力してください）"
        ),
        "answer": 500,
        "tolerance": 50,  # 許容誤差±50mL
        "explanation": (
            "安静時の1回換気量は体重あたり約6〜8mL/kgであり、成人では一般に約500mLとされています。"
        ),
    },
]

# フォームの作成
with st.form("exam_form"):
  user_answers = {}

  for q in questions:
    st.subheader(f"第 {q['id']} 問 【{q['category']}】")
    st.write(q["question"])

    if q["type"] == "choice":
      user_answers[q["id"]] = st.radio(
          "解答を選択してください", q["options"], key=f"q_{q['id']}"
      )
    elif q["type"] == "numeric":
      user_answers[q["id"]] = st.number_input(
          "数値を入力", value=0, key=f"q_{q['id']}"
      )

    st.write("---")

  # 採点ボタン
  submit_button = st.form_submit_button("解答を送信して採点する")

if submit_button:
  st.session_state.submitted = True
  correct_count = 0

  st.header("📊 採点結果・解説")

  for q in questions:
    user_ans = user_answers[q["id"]]
    is_correct = False

    if q["type"] == "choice":
      if user_ans == q["answer"]:
        is_correct = True
    elif q["type"] == "numeric":
      # 許容誤差を考慮した判定
      if (
          abs(user_ans - q["answer"]) <= q["tolerance"]
          if "tolerance" in q
          else user_ans == q["answer"]
      ):
        is_correct = True

    if is_correct:
      correct_count += 1
      st.success(f"第 {q['id']} 問：正解！")
    else:
      st.error(f"第 {q['id']} 問：不正解（正解は {q['answer']} です）")

    st.info(f"**【解説】** {q['explanation']}")
    st.write("---")

  st.metric(
      label="今回の正答率",
      value=f"{(correct_count / len(questions)) * 100:.1f}%",
  )
