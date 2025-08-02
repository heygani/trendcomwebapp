import streamlit as st
import sys
from PIL import Image
import io
import openai
import time
import csv
import re



from streamlit_oauth import OAuth2Component
from streamlit_cookies_manager import CookieManager
import datetime
from google import genai
from google.genai import types
import requests
import base64
import json
import mimetypes
import os

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
            st.info("キーワードやCSVからGemini APIを用いてWordPressに記事と画像を生成・投稿します。")

            # --- Configure APIs ---
            try:
                openai.api_key = st.secrets["openai"]["api_key"]
            except Exception as e:
                st.error(f"OpenAI APIキーの設定中にエラーが発生しました: {e}")
                st.stop()


            # --- Main Application Page ---
            st.header("📝 記事生成")

            uploaded_file = st.file_uploader(
                "CSVファイルをアップロードして複数記事を生成 (1列目: キーワード, 2列目: アフィリエイトHTML)",
                type=['csv']
            )
            st.markdown("--- **または** ---")

            keyword = st.text_area(
                "キーワードを入力してください（単一記事）：",
                height=150,
                value="メインキーワード:\n見出し用キーワードリスト: ",
                placeholder="例: メインキーワード: 最新のAI技術トレンド, 見出し用キーワードリスト: AI倫理, 機械学習, ディープラーニング, 自然言語処理, コンピュータビジョン, 強化学習, エッジAI, 量子AI, AIの未来, AIと社会"
            )
            
            affiliate_html = st.text_area(
                "アフィリエイト用HTMLコード（単一記事・オプション）：",
                height=100,
                placeholder="<a href='https://example.com' target='_blank'>商品リンク</a>"
            )
            
            if st.button("記事を生成してWordPressに投稿", key="generate_and_post_button"):
                articles_to_generate = []
                if uploaded_file is not None:
                    try:
                        stringio = io.StringIO(uploaded_file.getvalue().decode("utf-8-sig"))
                        csv_reader = csv.reader(stringio)
                        for row in csv_reader:
                            if not row or not row[0].strip(): continue
                            
                            keyword_data = row[0]
                            aff_html = row[1] if len(row) > 1 else ""
                            main_kw, heading_kws = "", ""

                            if "メインキーワード:" in keyword_data and "見出し用キーワードリスト:" in keyword_data:
                                parts = keyword_data.split("見出し用キーワードリスト:")
                                main_kw = parts[0].replace("メインキーワード:", "").strip()
                                heading_kws = parts[1].strip()
                            else:
                                main_kw = keyword_data.strip()
                            
                            articles_to_generate.append({
                                "main_keyword": main_kw,
                                "heading_keywords_list": heading_kws,
                                "affiliate_html": aff_html
                            })
                        
                        if not articles_to_generate:
                            st.error("CSVファイルが空か、内容が不正です。")
                            st.stop()
                    except Exception as e:
                        st.error(f"CSVファイルの読み込み中にエラーが発生しました: {e}")
                        st.stop()
                elif keyword.strip() and "メインキーワード:" in keyword:
                    main_kw, heading_kws = "", ""
                    if "メインキーワード:" in keyword and "見出し用キーワードリスト:" in keyword:
                        parts = keyword.split("見出し用キーワードリスト:")
                        main_kw = parts[0].replace("メインキーワード:", "").strip()
                        heading_kws = parts[1].strip()
                        articles_to_generate.append({
                            "main_keyword": main_kw,
                            "heading_keywords_list": heading_kws,
                            "affiliate_html": affiliate_html
                        })
                    else:
                        st.error("単一記事の入力形式が正しくありません。")
                        st.stop()
                else:
                    st.error("キーワードを入力するか、CSVファイルをアップロードしてください。")
                    st.stop()

                if articles_to_generate:
                    st.session_state.articles_to_generate = articles_to_generate
                    st.session_state.current_article_index = 0
                    st.session_state.process_status = "start_processing"
                    st.session_state.completed_articles = []
                    st.rerun()

            # --- Status Display and Backend Logic ---
            if "process_status" in st.session_state:
                status_placeholder = st.empty()
                current_index = st.session_state.get("current_article_index", 0)
                articles = st.session_state.get("articles_to_generate", [])
                total_articles = len(articles)
                progress_text = f"({current_index + 1}/{total_articles}) " if total_articles > 1 else ""

                status_map = {
                    "start_processing": "処理開始...",
                    "generating_outline": f"{progress_text}記事構成案を生成中...",
                    "generating_article": f"{progress_text}記事を生成中...",
                    "generating_images": f"{progress_text}画像を生成中...",
                    "posting_to_wordpress": f"{progress_text}WordPressに投稿中...",
                    "all_done": "全記事の投稿が完了しました！"
                }
                display_status = status_map.get(st.session_state.process_status, st.session_state.process_status)
                if st.session_state.process_status != "all_done":
                    status_placeholder.write(f"処理状況： {display_status}")

                current_main_keyword = st.session_state.get("main_keyword")

                def setup_gemini_client():
                    client = genai.Client(api_key=st.secrets["gemini"]["api_key"])
                    tools = [types.Tool(googleSearch=types.GoogleSearch())]
                    config = types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_budget=-1),
                        tools=tools
                    )
                    return client, config

                def generate_with_gemini(prompt, client=None, config=None):
                    if client is None or config is None:
                        client, config = setup_gemini_client()
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[types.Content(
                            role="user",
                            parts=[types.Part.from_text(text=prompt)]
                        )],
                        config=config
                    )
                    return response.text

                def handle_error(e, step):
                    error_message = f"失敗: {step}でエラーが発生しました。詳細: {str(e)}"
                    st.error(error_message)
                    st.session_state.completed_articles.append({"title": current_main_keyword, "status": error_message})
                    st.session_state.current_article_index += 1
                    st.session_state.process_status = "start_processing"
                    st.rerun()

                if st.session_state.process_status == "start_processing":
                    if current_index < total_articles:
                        article_data = articles[current_index]
                        st.session_state.main_keyword = article_data["main_keyword"]
                        st.session_state.heading_keywords_list = article_data["heading_keywords_list"]
                        st.session_state.affiliate_html = article_data["affiliate_html"]
                        st.session_state.generated_outline, st.session_state.generated_article, st.session_state.generated_images = None, None, []
                        st.session_state.process_status = "generating_outline"
                        st.rerun()
                    else:
                        st.session_state.process_status = "all_done"
                        st.rerun()

                current_heading_keywords_list = st.session_state.get("heading_keywords_list")

                if st.session_state.process_status == "generating_outline":
                    try:
                        midashi_prompt_template = st.secrets["prompts"]["midashi_prompt"]
                        midashi_prompt = midashi_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword).replace("｛チャットで入力した▼見出し用キーワードリスト｝", current_heading_keywords_list)
                        midashi_response = generate_with_gemini(midashi_prompt)
                        st.session_state.generated_outline = midashi_response
                        st.session_state.process_status = "generating_article"
                        st.rerun()
                    except Exception as e:
                        handle_error(e, "記事構成案生成")

                elif st.session_state.process_status == "generating_article":
                    try:
                        generated_outline = st.session_state.get("generated_outline")
                        if not generated_outline: raise ValueError("記事構成案が生成されていません。")
                        
                        article_prompt_template = st.secrets["prompts"]["article_prompt"]
                        article_prompt = article_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword).replace("｛チャットで入力した▼見出し用キーワードリスト｝", current_heading_keywords_list).replace("｛チャットで入力した▼記事構成案｝", generated_outline)
                        
                        article_response = generate_with_gemini(article_prompt)
                        st.session_state.generated_article = article_response
                        st.session_state.process_status = "generating_images"
                        st.rerun()
                    except Exception as e:
                        handle_error(e, "記事生成")

                elif st.session_state.process_status == "generating_images":
                    try:
                        st.write("--- 挿絵生成情報 ---")
                        sashie_prompt_template = st.secrets["prompts"]["sashie_pronpt"]
                        article_content_for_sashie = f"メインキーワード: {current_main_keyword}\n見出し用キーワードリスト: {current_heading_keywords_list}\n記事本文: {st.session_state.get('generated_article', '')}"
                        sashie_prompt = sashie_prompt_template.replace("{article_content}", article_content_for_sashie)
                        st.write("挿絵生成用プロンプトをGeminiで生成中...")
                        
                        sashie_response = generate_with_gemini(sashie_prompt)
                        dall_e_prompt = sashie_response.strip()
                        st.write(f"DALL-E用挿絵プロンプト: {dall_e_prompt}")
                        
                        st.session_state.generated_images = []
                        for i in range(1):
                            st.write(f"挿絵 {i+1}/6 を生成中...")
                            try:
                                response = openai.images.generate(model="dall-e-3", prompt=dall_e_prompt, n=1, size="1792x1024", response_format="url")
                                image_url = response.data[0].url
                                img_response = requests.get(image_url)
                                image_bytes = img_response.content
                                image = Image.open(io.BytesIO(image_bytes))
                                st.session_state.generated_images.append({'bytes': image_bytes, 'mime_type': "image/png", 'image': image})
                            except Exception as e:
                                st.warning(f"挿絵 {i+1} の生成中にエラー: {e}")
                                continue
                            time.sleep(1)
                        if not st.session_state.generated_images:
                            st.warning("挿絵の生成に失敗しましたが、記事の投稿は続行します。")
                        st.session_state.process_status = "posting_to_wordpress"
                        st.rerun()
                    except Exception as e:
                        st.warning(f"挿絵生成プロセス全体でエラーが発生しました: {e}。画像なしで投稿を続行します。")
                        st.session_state.process_status = "posting_to_wordpress"
                        st.rerun()

                elif st.session_state.process_status == "posting_to_wordpress":
                    try:
                        wp_url = st.secrets["wordpress"]["url"].rstrip('/')
                        wp_user = st.secrets["wordpress"]["username"]
                        wp_pass = st.secrets["wordpress"]["app_password"]
                        credentials = f"{wp_user}:{wp_pass}"
                        token = base64.b64encode(credentials.encode())
                        headers = {'Authorization': f'Basic {token.decode("utf-8")}'}
                        
                        uploaded_image_ids, image_urls = [], []
                        if st.session_state.get("generated_images"):
                            st.write("挿絵をWordPressにアップロード中...")
                            for i, image_data in enumerate(st.session_state.generated_images):
                                files = {'file': (f"sashie-{i+1}.png", image_data['bytes'], "image/png")}
                                media_data_payload = {'alt_text': f"{current_main_keyword}の挿絵{i+1}"}
                                upload_response = requests.post(f"{wp_url}/media", headers=headers, files=files, data=media_data_payload)
                                if upload_response.ok:
                                    media_data = upload_response.json()
                                    uploaded_image_ids.append(media_data['id'])
                                    image_urls.append(media_data['source_url'])
                                else:
                                    st.warning(f"挿絵 {i+1} のアップロードに失敗: {upload_response.text}")

                        article_content = st.session_state.generated_article
                        if image_urls:
                            lines = article_content.split('\n')
                            new_lines = []
                            image_index = 0
                            for line in lines:
                                new_lines.append(line)
                                if '<h3>' in line and image_index < len(image_urls):
                                    new_lines.append(f'<img src="{image_urls[image_index]}" alt="{current_main_keyword}の挿絵{image_index+1}" style="max-width: 100%; height: auto; margin: 20px 0;" />')
                                    image_index += 1
                            article_content = '\n'.join(new_lines)
                        
                        lines = article_content.split('\n')
                        if len(lines) > 2: article_content = '\n'.join(lines[1:-1])
                        
                        article_content = re.sub(r'\[\\d+(?:\\s*,\\s*\\d+)*\]', '', article_content)

                        affiliate_html = st.session_state.get("affiliate_html", "")
                        if affiliate_html.strip():
                            wrapped_affiliate_html = f"<!-- wp:html -->\n{affiliate_html}\n<!-- /wp:html -->"
                            article_content = article_content.replace("{アフィリエイト}", wrapped_affiliate_html)
                        else:
                            article_content = article_content.replace("{アフィリエイト}", "")

                        title_prompt_template = st.secrets["prompts"]["title_prompt"]
                        title_prompt = title_prompt_template.replace("｛チャットで入力した▼メインキーワード｝", current_main_keyword).replace("{article_content}", article_content)
                        title_response = generate_with_gemini(title_prompt)
                        title = title_response.strip()

                        #カテゴリー生成
                        category_prompt_template = st.secrets["prompts"]["category_prompt"]
                        category_prompt = category_prompt_template.replace("{article_content}", article_content)
                        category_response = generate_with_gemini(category_prompt)
                        category = category_response.strip()
                        # カテゴリー名称のリスト
                        category_names = ["PC家電", "生活雑貨", "美容", "食品", "飲料", "キッチン", "インテリア", "ファッション", "アパレル", "キッズベビー", "趣味", "ホビー", "ゲーム"]
                        if category not in category_names:
                            category = "どこで買える"

                        # カテゴリーの処理
                        try:
                            # カテゴリーの取得
                            categories_response = requests.get(f"{wp_url}/categories", headers=headers, params={'per_page': 100})
                            if not categories_response.ok:
                                raise Exception(f"カテゴリー情報の取得に失敗: {categories_response.text}")
                            
                            categories = categories_response.json()
                            category_id = None
                            
                            # 既存のカテゴリーから検索
                            for cat in categories:
                                if cat['name'].lower() == category.lower():  # 大文字小文字を区別しない比較
                                    category_id = cat['id']
                                    break
                            
                            # カテゴリーが存在しない場合は新規作成
                            if category_id is None:
                                new_category = {
                                    'name': category,
                                    'description': f'「{category}」に関する記事一覧'
                                }
                                create_response = requests.post(f"{wp_url}/categories", headers=headers, json=new_category)
                                if not create_response.ok:
                                    raise Exception(f"カテゴリーの作成に失敗: {create_response.text}")
                                category_id = create_response.json()['id']
                            
                            # カテゴリーIDをセッションに保存
                            st.session_state.generated_category_id = category_id
                            
                        except Exception as e:
                            st.warning(f"カテゴリー処理中にエラー: {str(e)}")
                            category_id = None
                        
                        # 投稿データの作成
                        post = {
                            'title': title,
                            'content': article_content,
                            'status': 'draft',
                            'featured_media': uploaded_image_ids[0] if uploaded_image_ids else 0,
                            'categories': [category_id] if category_id else []
                        }
                        
                        response = requests.post(f"{wp_url}/posts", headers=headers, json=post)

                        if response.ok:
                            st.session_state.completed_articles.append({"title": title, "status": "成功"})
                        else:
                            error_message = response.text
                            if "text/html" in response.headers.get("Content-Type", ""): error_message = "WordPressサーバーから予期せぬHTML応答 (404等)"
                            st.session_state.completed_articles.append({"title": title or current_main_keyword, "status": f"失敗: {error_message[:100]}"})
                        
                        st.session_state.current_article_index += 1
                        st.session_state.process_status = "start_processing"
                        st.rerun()
                    except Exception as e:
                        handle_error(e, "WordPress投稿")

                elif st.session_state.process_status == "all_done":
                    status_placeholder.empty()
                    st.success("全ての処理が完了しました！")
                    st.markdown("### 処理結果")
                    completed = st.session_state.get("completed_articles", [])
                    if completed:
                        for result in completed:
                            st.write(f"- **記事:** {result.get('title', 'N/A')}  **ステータス:** {result.get('status', 'N/A')}")
                    else:
                        st.write("処理された記事はありません。")
                    
                    for key in list(st.session_state.keys()):
                        if key not in ['token']:
                            del st.session_state[key]
                    if st.button("リセット"):
                        st.rerun()


                if "generated_article" in st.session_state and st.session_state.generated_article and st.session_state.process_status != 'all_done':
                    with st.expander("現在生成中の記事プレビュー", expanded=True):
                        st.markdown("#### 生成された挿絵")
                        if st.session_state.get("generated_images"):
                            for i, image_data in enumerate(st.session_state.generated_images):
                                st.image(image_data['image'], caption=f"挿絵 {i+1}")
                        else:
                            st.write("挿絵はありません。")
                        st.markdown("#### 生成された記事")
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
