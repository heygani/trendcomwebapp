import streamlit as st
import sys
from PIL import Image
import io
import openai

st.write(f"**診断情報:**")
st.write(f"* Python実行パス: `{sys.executable}`")
try:
    import google.generativeai
    st.write(f"* google-generativeai バージョン: `{google.generativeai.__version__}`")
except ImportError:
    st.write("* google-generativeai はこの環境にインストールされていません。")
st.write("--- ")

from streamlit_oauth import OAuth2Component
from streamlit_cookies_manager import CookieManager
import datetime
import google.generativeai as genai
from google.generativeai import types
import requests
import base64
import json
import mimetypes
import os
from google import genai
from google.genai import types


st.set_page_config(
    page_title="WordPress Article Generator",
    page_icon="🤖",
    layout="centered",
)

st.title("WordPress Article Generator 🤖")

# --- Configuration ---
CLIENT_ID = st.secrets.get("google_oauth", {}).get("client_id")
CLIENT_SECRET = st.secrets.get("google_oauth", {}).get("client_secret")
REDIRECT_URI = st.secrets.get("google_oauth", {}).get("redirect_uri")
TARGET_EMAIL = st.secrets.get("authentication", {}).get("target_user_email")
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# --- Check for Secrets ---
if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, TARGET_EMAIL]):
    st.error("必要な認証情報がsecrets.tomlに設定されていません。ファイルを確認してください。")
else:
    # Cookie Managerを初期化します
    cookies = CookieManager()

    # --- Authentication ---
    oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_ENDPOINT, TOKEN_ENDPOINT)

    if "token" not in st.session_state:
        if cookies.ready():
            token_from_cookie = cookies.get("token")
            if token_from_cookie:
              try:
                  st.session_state.token = json.loads(token_from_cookie)
              except json.JSONDecodeError:
                  st.session_state.token = None
            else:
              st.session_state.token = None
        else:
            st.session_state.token = None
            st.info("Cookie Manager is initializing. Please wait...")
            st.stop()
            # st.experimental_rerun()  # st.experimental_rerun() は削除
    
    # Ensure st.session_state.token is a dictionary if it exists
    if st.session_state.token and not isinstance(st.session_state.token, dict):
        st.error("認証トークンが不正な形式です。再ログインしてください。")
        st.session_state.token = None
        st.rerun()
        st.stop()

    if st.session_state.token is None:
        result = oauth2.authorize_button(
            "Sign in with Google",
            redirect_uri=REDIRECT_URI,
            scope="email",
        )
        if result and "token" in result:
            st.session_state.token = result.get("token")
            cookies["token"] = json.dumps(st.session_state.token)
            cookies.save()
            st.rerun()
    
    if st.session_state.token:
        raw_token_data = st.session_state.token
        parsed_token_data = None

        if isinstance(raw_token_data, str):
            try:
                parsed_token_data = json.loads(raw_token_data)
            except json.JSONDecodeError:
                st.error("認証トークンが不正な形式です。再ログインしてください。")
                st.session_state.token = None
                st.rerun()
                st.stop()
        elif isinstance(raw_token_data, dict):
            parsed_token_data = raw_token_data
        
        if not isinstance(parsed_token_data, dict):
            st.error("認証トークンが不正な形式です。再ログインしてください。")
            st.session_state.token = None
            st.rerun()
            st.stop()
        
        id_token = parsed_token_data.get("id_token")
        user_email = None
        if id_token:
            try:
                payload = id_token.split('.')[1]
                padded_payload = payload + '=' * (4 - len(payload) % 4)
                decoded_payload = base64.urlsafe_b64decode(padded_payload)
                user_data = json.loads(decoded_payload)
                user_email = user_data.get("email")
            except Exception as e:
                st.error(f"トークンの解析中にエラーが発生しました: {e}")
                user_email = None

        if user_email == TARGET_EMAIL:
            st.success(f"Logged in as {user_email}")
            st.info("キーワードからGemini APIを用いてWordPressに記事と画像を生成・投稿します。")

            # --- Main Application Page ---
            st.header("📝 記事生成")
            keyword = st.text_area(
                "キーワードを入力してください：",
                height=150,
                value="メインキーワード:\n見出し用キーワードリスト: ",
                placeholder="例: メインキーワード: 最新のAI技術トレンド, 見出し用キーワードリスト: AI倫理, 機械学習, ディープラーニング, 自然言語処理, コンピュータビジョン, 強化学習, エッジAI, 量子AI, AIの未来, AIと社会"
            )
            
            affiliate_html = st.text_area(
                "アフィリエイト用HTMLコード（オプション）：",
                height=100,
                placeholder="例: <a href='https://example.com' target='_blank'>商品リンク</a>"
            )
            
            if st.button("記事を生成してWordPressに投稿", key="generate_and_post_button"):
                if keyword:
                    # Parse the input string
                    main_keyword = ""
                    heading_keywords_list = ""

                    if "メインキーワード:" in keyword and "見出し用キーワードリスト:" in keyword:
                        parts = keyword.split("見出し用キーワードリスト:")
                        main_keyword_part = parts[0].replace("メインキーワード:", "").strip()
                        heading_keywords_list_part = parts[1].strip()

                        main_keyword = main_keyword_part
                        heading_keywords_list = heading_keywords_list_part
                    else:
                        st.error("入力形式が正しくありません。「メインキーワード: ... , 見出し用キーワードリスト: ...」の形式で入力してください。")
                        st.stop()

                    st.session_state.main_keyword = main_keyword
                    st.session_state.heading_keywords_list = heading_keywords_list
                    st.session_state.affiliate_html = affiliate_html
                    st.session_state.process_status = "記事構成案を生成中..."
                    st.rerun()
                else:
                    st.error("キーワードを入力してください。")

            # --- Status Display and Backend Logic ---
            if "process_status" in st.session_state:
                status_placeholder = st.empty()
                status_placeholder.write(f"処理状況： {st.session_state.process_status}")
                current_main_keyword = st.session_state.get("main_keyword")
                current_heading_keywords_list = st.session_state.get("heading_keywords_list")

                if st.session_state.process_status == "記事構成案を生成中...":
                    try:
                        os.environ["GOOGLE_API_KEY"] = st.secrets["gemini"]["api_key"]
                        client = genai.Client()
                        grounding_tool = types.Tool(google_search=types.GoogleSearch())
                        generation_config = types.GenerateContentConfig(tools=[grounding_tool])

                        midashi_prompt_template = st.secrets["prompts"]["midashi_prompt"]
                        midashi_prompt = midashi_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword)
                        midashi_prompt = midashi_prompt.replace("｛チャットで入力した▼見出し用キーワードリスト｝", current_heading_keywords_list)

                        midashi_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=midashi_prompt,
                            config=generation_config,
                        )

                        st.session_state.generated_outline = midashi_response.text
                        st.session_state.process_status = "記事を生成中..."
                        st.rerun()
                    except Exception as e:
                        st.session_state.process_status = f"エラー: 記事構成案生成中に問題が発生しました - {e}"
                        st.rerun()

                elif st.session_state.process_status == "記事を生成中...":
                    try:
                        os.environ["GOOGLE_API_KEY"] = st.secrets["gemini"]["api_key"]
                        client = genai.Client()
                        grounding_tool = types.Tool(google_search=types.GoogleSearch())
                        generation_config = types.GenerateContentConfig(tools=[grounding_tool])

                        generated_outline = st.session_state.get("generated_outline")

                        if not generated_outline:
                            st.error("記事構成案が生成されていません。")
                            st.session_state.process_status = "エラー: 記事構成案がありません。"
                            st.rerun()
                        
                        article_prompt_template = st.secrets["prompts"]["article_prompt"]
                        article_prompt = article_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword)
                        article_prompt = article_prompt.replace("｛チャットで入力した▼見出し用キーワードリスト｝", current_heading_keywords_list)
                        article_prompt = article_prompt.replace("｛チャットで入力した▼記事構成案｝", generated_outline)

                        article_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=article_prompt,
                            config=generation_config,
                        )

                        st.session_state.generated_article = article_response.text
                        st.session_state.process_status = "画像を生成中..."
                        st.rerun()
                    except Exception as e:
                        st.session_state.process_status = f"エラー: 記事生成中に問題が発生しました - {e}"
                        st.rerun()

                elif st.session_state.process_status == "画像を生成中...":
                    try:
                        st.write("--- 挿絵生成情報 ---")
                        # OpenAI APIキーをsecretsから取得
                        openai_api_key = st.secrets["openai"]["api_key"]
                        # 挿絵生成用のプロンプトを作成
                        sashie_prompt_template = st.secrets["prompts"]["sashie_pronpt"]
                        article_content_for_sashie = f"メインキーワード: {current_main_keyword}\n見出し用キーワードリスト: {current_heading_keywords_list}\n記事本文: {st.session_state.get('generated_article', '')}"
                        sashie_prompt = sashie_prompt_template.replace("{article_content}", article_content_for_sashie)
                        st.write("挿絵生成用プロンプトをGeminiで生成中...")
                        # Geminiで挿絵用プロンプトを生成
                        os.environ["GOOGLE_API_KEY"] = st.secrets["gemini"]["api_key"]
                        client = genai.Client()
                        sashie_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=sashie_prompt,
                        )
                        dall_e_prompt = sashie_response.text.strip()
                        st.write(f"DALL-E用挿絵プロンプト: {dall_e_prompt}")
                        # 6つの挿絵を生成
                        st.session_state.generated_images = []
                        for i in range(6):
                            st.write(f"挿絵 {i+1}/6 を生成中...")
                            response = openai.OpenAI(api_key=openai_api_key).images.generate(
                                model="dall-e-3",
                                prompt=dall_e_prompt,
                                n=1,
                                size="1792x1024",
                                response_format="url",
                            )
                            image_url = response.data[0].url
                            image_bytes = None
                            image = None
                            mime_type = "image/png"
                            # 画像をダウンロード
                            try:
                                img_response = requests.get(image_url)
                                image_bytes = img_response.content
                                image = Image.open(io.BytesIO(image_bytes))
                            except Exception as e:
                                st.error(f"画像のダウンロードまたはPILで画像化に失敗: {e}")
                                image = None
                            if image and image_bytes:
                                st.write(f"挿絵 {i+1} 生成成功。受信データサイズ: {len(image_bytes)} bytes, MIMEタイプ: {mime_type}")
                                st.session_state.generated_images.append({
                                    'bytes': image_bytes,
                                    'mime_type': mime_type,
                                    'index': i+1,
                                    'image': image
                                })
                            else:
                                st.error(f"挿絵 {i+1} の生成に失敗しました。")
                        if len(st.session_state.generated_images) > 0:
                            st.write(f"挿絵生成完了: {len(st.session_state.generated_images)}個の挿絵を生成しました。")
                            st.session_state.process_status = "WordPressに投稿中..."
                            st.rerun()
                        else:
                            st.error("挿絵の生成に失敗しました。")
                            st.session_state.process_status = "エラー: 挿絵生成に失敗しました。"
                    except Exception as e:
                        st.error("挿絵生成API呼び出しで例外が発生しました。以下に詳細を示します：")
                        st.exception(e)
                        st.session_state.process_status = f"エラー: 挿絵生成中に致命的な問題が発生しました。"

                elif st.session_state.process_status == "WordPressに投稿中...":
                    try:
                        wp_url = st.secrets["wordpress"]["url"].rstrip('/')
                        wp_user = st.secrets["wordpress"]["username"]
                        wp_pass = st.secrets["wordpress"]["app_password"]
                        credentials = f"{wp_user}:{wp_pass}"
                        token = base64.b64encode(credentials.encode())
                        headers = {'Authorization': f'Basic {token.decode("utf-8")}'}
                        
                        uploaded_image_ids = []
                        image_urls = []
                        generated_images = st.session_state.get("generated_images", [])

                        if generated_images:
                            st.write("挿絵をWordPressにアップロード中...")
                            for i, image_data in enumerate(generated_images):
                                st.write(f"挿絵 {i+1}/6 をアップロード中...")
                                image_bytes = image_data['bytes']
                                mime_type = image_data['mime_type']
                                extension = mimetypes.guess_extension(mime_type) or ".png"
                                filename = f"sashie-{i+1}{extension}"

                                # WordPress REST APIでalt_textを含めるためにmultipart/form-dataを使用
                                files = {
                                    'file': (filename, image_bytes, mime_type)
                                }
                                media_data_payload = {
                                    'alt_text': f"{current_main_keyword}の挿絵{i+1}" # ここで代替テキストを設定
                                }
                                upload_response = requests.post(f"{wp_url}/media", headers=headers, files=files, data=media_data_payload)
                                
                                if upload_response.status_code >= 200 and upload_response.status_code < 300:
                                    media_data = upload_response.json()
                                    uploaded_image_ids.append(media_data['id'])
                                    image_urls.append(media_data['source_url'])
                                    st.write(f"挿絵 {i+1} のアップロードが完了しました。")
                                else:
                                    st.warning(f"挿絵 {i+1} のアップロードに失敗しました: {upload_response.text}")

                        article_content = st.session_state.generated_article
                        if image_urls:
                            # 記事の適切な位置に挿絵を挿入
                            lines = article_content.split('\n')
                            new_lines = []
                            image_index = 0
                            
                            for line in lines:
                                new_lines.append(line)
                                # H3見出しの後に挿絵を挿入（ただし最初のH3の前には挿入しない）
                                if '<h3>' in line and image_index < len(image_urls):
                                    new_lines.append(f'<img src="{image_urls[image_index]}" alt="{current_main_keyword}の挿絵{image_index+1}" style="max-width: 100%; height: auto; margin: 20px 0;" />')
                                    image_index += 1
                            
                            article_content = '\n'.join(new_lines)
                        
                        # Remove the first and last lines from article_content
                        lines = article_content.split('\n')
                        if len(lines) > 2:  # Ensure there are at least 3 lines to remove first and last
                            article_content = '\n'.join(lines[1:-1])
                        elif len(lines) == 2: # If only two lines, make it empty
                            article_content = ""
                        elif len(lines) == 1: # If only one line, make it empty
                            article_content = ""

                        # Remove annotation tags like [1], [1,2,3], etc.
                        import re
                        # Pattern to match [数字] or [数字,数字,数字] format (including spaces)
                        annotation_pattern = r'\[\d+(?:\s*,\s*\d+)*\]'
                        article_content = re.sub(annotation_pattern, '', article_content)

                        # Handle affiliate HTML replacement
                        affiliate_html = st.session_state.get("affiliate_html", "")
                        if affiliate_html.strip():
                            # Replace {アフィリエイト} with the provided HTML wrapped in WordPress HTML block
                            wrapped_affiliate_html = f"<!-- wp:html -->\n{affiliate_html}\n<!-- /wp:html -->"
                            article_content = article_content.replace("{アフィリエイト}", wrapped_affiliate_html)
                        else:
                            # Remove {アフィリエイト} if no affiliate HTML is provided
                            article_content = article_content.replace("{アフィリエイト}", "")

                        # Configure the client
                        client = genai.Client()

                        # Define the grounding tool
                        grounding_tool = types.Tool(
                            google_search=types.GoogleSearch()
                        )

                        # Configure generation settings
                        generation_config = types.GenerateContentConfig(
                            tools=[grounding_tool]
                        )

                        # Generate title using Gemini
                        title_prompt_template = st.secrets["prompts"]["title_prompt"]
                        title_prompt = title_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword)
                        title_prompt = title_prompt_template.replace("{article_content}", article_content)

                        title_response = client.models.generate_content(
                            model="gemini-2.5-pro",
                            contents=title_prompt,
                            config=generation_config,
                        )
                        title = title_response.text.strip()

                        post = {
                            'title': title,
                            'content': article_content,
                            'status': 'draft',
                            'featured_media': uploaded_image_ids[0] if uploaded_image_ids else 0
                        }
                        
                        st.write("記事をWordPressに投稿中...")
                        post_url = f"{wp_url}/posts"
                        response = requests.post(post_url, headers=headers, json=post)

                        if response.status_code >= 200 and response.status_code < 300:
                            st.session_state.process_status = "完了！"
                        else:
                            error_message = response.text
                            if "text/html" in response.headers.get("Content-Type", ""):
                                error_message = "WordPressサーバーから予期せぬHTML応答がありました。secrets.tomlのURLが間違っている可能性があります (404 Not Found)。"
                            st.session_state.process_status = f"エラー: WordPress投稿失敗. URL: {post_url}, Code: {response.status_code}, Msg: {error_message[:500]}"
                        st.rerun()

                    except Exception as e:
                        st.session_state.process_status = f"エラー: WordPress投稿中に予期せぬ問題が発生しました - {e}"
                        st.rerun()

                if "generated_article" in st.session_state:
                    st.markdown("### 生成された記事プレビュー")
                    if st.session_state.get("generated_images"):
                        st.markdown("#### 生成された挿絵")
                        for i, image_data in enumerate(st.session_state.generated_images):
                            st.image(image_data['image'], caption=f"挿絵 {i+1}")
                    st.markdown(st.session_state.generated_article)

        elif user_email:
            st.error(f"アクセスが許可されていません。現在 {user_email} でログインしています。")
            if st.button("ログアウト"):
                st.session_state.token = None
                cookies.delete("token")
                st.rerun()
        else:
            st.error("Googleアカウントのメールアドレスを取得できませんでした。")
            if st.button("再ログイン"):
                st.session_state.token = None
                st.rerun()