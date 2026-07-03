import io
import os

import streamlit as st
from PIL import Image

st.set_page_config(page_title="胚検出・評価AIアプリ", layout="centered")
st.title("胚検出・評価AIシステム")
st.write("画像をアップロードすると、AIが自動で解析して評価・検出を行います。")

MODEL_ID = "embryo_detection-chld1-2-rfdetr-small-t1"

try:
    from inference_sdk import InferenceHTTPClient
except ImportError:
    st.error(
        "必要なパッケージがインストールされていません。"
        "ターミナルで `pip install -r requirements.txt` を実行してください。"
    )
    st.stop()


def get_api_key() -> str:
    if "ROBOFLOW_API_KEY" in st.secrets:
        return st.secrets["ROBOFLOW_API_KEY"]
    return os.environ.get("ROBOFLOW_API_KEY", "")


API_KEY = get_api_key()

if not API_KEY or API_KEY == "API_KEY":
    st.warning("Roboflow APIキーが設定されていません。")
    st.info(
        "`.streamlit/secrets.toml` に次の行を追加してください:\n\n"
        'ROBOFLOW_API_KEY = "あなたのAPIキー"'
    )
    st.stop()


@st.cache_resource
def get_inference_client(api_key: str) -> InferenceHTTPClient:
    return InferenceHTTPClient(
        api_url="https://serverless.roboflow.com",
        api_key=api_key,
    )


client = get_inference_client(API_KEY)


def load_uploaded_image(uploaded_file) -> Image.Image:
    uploaded_file.seek(0)
    image = Image.open(io.BytesIO(uploaded_file.read()))
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def extract_predictions(result) -> list:
    if isinstance(result, list):
        if result and isinstance(result[0], dict):
            return result[0].get("predictions", [])
        return []

    if isinstance(result, dict):
        if "predictions" in result:
            return result.get("predictions", [])
        nested = result.get("result")
        if isinstance(nested, dict):
            return nested.get("predictions", [])

    return []


uploaded_file = st.file_uploader(
    "胚の画像を選択してください（JPG, JPEG, PNG）",
    type=["jpg", "jpeg", "png"],
)

if uploaded_file is not None:
    try:
        image = load_uploaded_image(uploaded_file)
    except Exception as e:
        st.error(f"画像の読み込みに失敗しました: {e}")
        st.stop()

    st.image(image, caption="アップロードされた画像", use_container_width=True)

    if st.button("AIで解析・評価する", type="primary"):
        with st.spinner("AIが画像を解析中..."):
            try:
                result = client.infer(image, model_id=MODEL_ID)
                predictions = extract_predictions(result)

                st.success("解析が完了しました！")

                if not predictions:
                    st.info("対象のオブジェクトは検出されませんでした。")
                else:
                    st.subheader("検出結果（評価データ）")
                    st.write(f"検出数: {len(predictions)} 個")
                    st.json(result)

            except Exception as e:
                st.error(f"解析中にエラーが発生しました: {e}")
                st.caption("APIキー・モデルID・ネットワーク接続を確認してください。")
