import os
import zipfile
import shutil
from typing import List, Dict, Optional
from PIL import Image
import openai
from io import BytesIO
import requests
from flask import Flask, request, jsonify, send_file, render_template

# OpenAI APIキーの設定（環境変数を使用することを推奨）
def get_openai_api_key():
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY 環境変数が設定されていません。APIキーを設定してください。")
    return api_key

openai.api_key = get_openai_api_key()

app = Flask(__name__)

# ダウンロードした画像を/tmpフォルダに保存
def download_image_from_url(url: str, filename: str) -> str:
    """指定されたURLから画像をダウンロードし、/tmpに保存してパスを返します。"""
    try:
        img_data = requests.get(url).content
        img_path = os.path.join('/tmp', filename)
        with open(img_path, 'wb') as f:
            f.write(img_data)
        return img_path
    except Exception as e:
        print(f"画像のダウンロード中にエラーが発生しました: {e}")
        return None

def generate_image(prompt: str, size: str = "1024x1024", aspect_ratio: Optional[str] = None, filename_prefix: str = "image") -> str:
    """
    OpenAIのDALL-E APIを使用して画像を生成し、/tmpに保存します。

    Parameters:
        prompt (str): 画像生成のためのテキストプロンプト。
        size (str, optional): 画像のサイズ（デフォルトは"1024x1024"）。
        aspect_ratio (str, optional): アスペクト比（例："16:9"）。指定された場合、サイズを調整。
        filename_prefix (str): 生成された画像のファイル名のプレフィックス。

    Returns:
        str: 生成された画像ファイルのパス。
    """
    try:
        response = openai.Image.create(prompt=prompt, n=1, size=size)
        image_url = response['data'][0]['url']
        filename = f"{filename_prefix}.png"
        image_path = download_image_from_url(image_url, filename)

        return image_path
    except Exception as e:
        print(f"画像生成中にエラーが発生しました: {e}")
        return None

def validate_image_count(count: int):
    """画像の枚数が0〜10の範囲内か確認します。"""
    if not (0 <= count <= 10):
        raise ValueError(f"画像の枚数は0から10の間で指定してください。現在の値: {count}")

def generate_images(prompts: List[Dict]) -> List[str]:
    """
    指定されたプロンプトに基づいて画像を生成し、ファイルパスを返します。

    Parameters:
        prompts (List[Dict]): 画像生成のためのプロンプト情報のリスト。

    Returns:
        List[str]: 生成された画像ファイルのパスのリスト。
    """
    generated_image_paths = []
    for prompt_info in prompts:
        prompt = prompt_info['prompt']
        size = prompt_info.get('size', "1024x1024")
        aspect_ratio = prompt_info.get('aspect_ratio')
        count = prompt_info['count']
        filename_prefix = prompt_info['filename_prefix']

        validate_image_count(count)

        # 0の場合はスキップ
        if count == 0:
            continue

        for i in range(count):
            image_path = generate_image(prompt, size, aspect_ratio, f"{filename_prefix}_{i + 1}")
            if image_path:
                generated_image_paths.append(image_path)

    return generated_image_paths

def save_files(base_dir: str, html_content: str, css_files: Dict[str, str], img_files: Dict[str, str]):
    """
    HTML、CSS、画像ファイルを指定されたディレクトリに保存します。

    Parameters:
        base_dir (str): 保存先のベースディレクトリ（/tmp）。
        html_content (str): HTMLコンテンツ。
        css_files (Dict[str, str]): CSSファイル名とその内容。
        img_files (Dict[str, str]): 画像ファイルのソースパスと保存先のファイル名。
    """
    # HTMLファイル保存
    with open(os.path.join(base_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    # CSSファイル保存
    os.makedirs(os.path.join(base_dir, 'css'), exist_ok=True)
    for file_name, content in css_files.items():
        with open(os.path.join(base_dir, 'css', file_name), 'w', encoding='utf-8') as f:
            f.write(content)

    # 画像ファイル保存
    os.makedirs(os.path.join(base_dir, 'img'), exist_ok=True)
    for src, dest in img_files.items():
        shutil.move(src, os.path.join(base_dir, 'img', dest))

def create_zip(base_dir: str) -> str:
    """
    指定されたディレクトリをZIPファイルとして圧縮します。

    Parameters:
        base_dir (str): 圧縮するディレクトリ（/tmp）。

    Returns:
        str: 圧縮されたZIPファイルのパス。
    """
    zip_file_path = f'{base_dir}.zip'
    shutil.make_archive(zip_file_path.replace(".zip", ""), 'zip', base_dir)
    return zip_file_path

def create_lp(base_dir: str, company_info: Dict, sections: List[Dict], image_prompts: List[Dict], css_config: Dict) -> str:
    """
    LPを作成し、ZIPファイルとして出力します。

    Parameters:
        base_dir (str): 保存先のベースディレクトリ（/tmp）。
        company_info (Dict): 企業情報。
        sections (List[Dict]): セクション情報のリスト。
        image_prompts (List[Dict]): 画像生成プロンプトのリスト。
        css_config (Dict): CSS設定。

    Returns:
        str: 作成されたZIPファイルのパス。
    """
    try:
        # 画像生成
        generated_image_paths = generate_images(image_prompts)

        # HTMLコンテンツの生成（テンプレートエンジンや手動でHTMLを作成）
        html_content = render_template('index.html', company_info=company_info, sections=sections)

        # CSSファイルの作成
        css_files = {
            'reset.css': "/* リセットCSS */\n* { margin: 0; padding: 0; box-sizing: border-box; }",
            'style.css': f"/* スタイルCSS */\nbody {{ font-family: {css_config['font_family']}; background-color: #ffffff; }}"
        }

        # 生成された画像とHTML/CSSを元にLP構造を作成
        img_files = {generated_image_paths[i]: f'image_{i + 1}.png' for i in range(len(generated_image_paths))}

        # ディレクトリ構造を作成
        os.makedirs(base_dir, exist_ok=True)

        # ファイルを保存
        save_files(base_dir, html_content, css_files, img_files)

        # ZIPファイルを作成
        return create_zip(base_dir)

    except Exception as e:
        print(f"LP作成中にエラーが発生しました: {e}")
        return ""

# Flaskルート
@app.route('/generate_lp', methods=['POST'])
def generate_lp_endpoint():
    try:
        data = request.get_json()

        # リクエストデータからテキストを取得
        request_text = data.get('text', '')

        # キーワードをチェック
        if "LPを作ってください" in request_text:
            # 必要なデータを取得
            company_info = data['company_info']
            sections = data['sections']
            image_prompts = data['image_prompts']
            css_config = data['css_config']
            base_dir = '/tmp/lp_structure'

            # LP生成
            zip_file_path = create_lp(base_dir, company_info, sections, image_prompts, css_config)

            if not zip_file_path:
                return jsonify({'error': 'LP作成に失敗しました。'}), 500

            return send_file(zip_file_path, as_attachment=True)

        return jsonify({'error': '無効なリクエストです。'}), 400

    except KeyError as e:
        return jsonify({'error': f"欠けているキー: {e}"}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/submit_json', methods=['POST'])
def submit_json():
    # JSONデータを受け取り、company_infoを作成
    data = request.get_json()
    company_info = {
        'name': data.get('name'),
        'description': data.get('description'),
        'tel': data.get('tel'),
        'hours': data.get('hours'),
        'holidays': data.get('holidays'),
        'address': data.get('address')
    }

    # テンプレートに company_info を渡して表示
    return render_template('index.html', company_info=company_info)

if __name__ == '__main__':
    app.run(debug=True)
