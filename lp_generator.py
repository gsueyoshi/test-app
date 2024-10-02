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

def download_image_from_url(url: str) -> Image.Image:
    """指定されたURLから画像をダウンロードしてPILのImageオブジェクトを返します。"""
    try:
        img_data = requests.get(url).content
        return Image.open(BytesIO(img_data))
    except Exception as e:
        print(f"画像のダウンロード中にエラーが発生しました: {e}")
        return Image.new('RGB', (1024, 1024), color=(200, 200, 200))  # デフォルト画像を返す

def generate_image(prompt: str, size: str = "1024x1024", aspect_ratio: Optional[str] = None) -> Image.Image:
    """
    OpenAIのDALL-E APIを使用して画像を生成します。

    Parameters:
        prompt (str): 画像生成のためのテキストプロンプト。
        size (str, optional): 画像のサイズ（デフォルトは"1024x1024"）。
        aspect_ratio (str, optional): アスペクト比（例："16:9"）。指定された場合、サイズを調整。

    Returns:
        Image.Image: 生成されたPIL Imageオブジェクト。
    """
    try:
        response = openai.Image.create(prompt=prompt, n=1, size=size)
        image_url = response['data'][0]['url']
        image = download_image_from_url(image_url)

        if aspect_ratio:
            aspect_width, aspect_height = map(int, aspect_ratio.split(':'))
            new_height = int((aspect_height / aspect_width) * image.width)
            image = image.resize((image.width, new_height))

        return image
    except Exception as e:
        print(f"画像生成中にエラーが発生しました: {e}")
        return Image.new('RGB', (1024, 1024), color=(200, 200, 200))  # エラー時のデフォルト画像

def validate_image_count(count: int):
    """画像の枚数が1〜10の範囲内か確認します。"""
    if not (1 <= count <= 10):
        raise ValueError(f"画像の枚数は1から10の間で指定してください。現在の値: {count}")

def generate_images(prompts: List[Dict]) -> List[str]:
    """
    指定されたプロンプトに基づいて画像を生成し、ファイルパスを返します。

    Parameters:
        prompts (List[Dict]): 画像生成のためのプロンプト情報のリスト。

    Returns:
        List[str]: 生成された画像ファイルのパスのリスト。
    """
    generated_image_paths = []
    img_folder = 'generated_images'
    os.makedirs(img_folder, exist_ok=True)

    for prompt_info in prompts:
        prompt = prompt_info['prompt']
        size = prompt_info.get('size', "1024x1024")
        aspect_ratio = prompt_info.get('aspect_ratio')
        count = prompt_info['count']
        filename_prefix = prompt_info['filename_prefix']

        validate_image_count(count)

        for i in range(count):
            image = generate_image(prompt, size, aspect_ratio)
            image_filename = f"{filename_prefix}_{i + 1}.png"
            image_path = os.path.join(img_folder, image_filename)
            image.save(image_path)
            generated_image_paths.append(image_path)

    return generated_image_paths

def create_directory_structure(base_dir: str, subdirs: List[str]):
    """指定されたディレクトリとサブディレクトリを作成します。"""
    os.makedirs(base_dir, exist_ok=True)
    for subdir in subdirs:
        os.makedirs(os.path.join(base_dir, subdir), exist_ok=True)

def save_files(base_dir: str, html_content: str, css_files: Dict[str, str], img_files: Dict[str, str]):
    """
    HTML、CSS、画像ファイルを指定されたディレクトリに保存します。

    Parameters:
        base_dir (str): 保存先のベースディレクトリ。
        html_content (str): HTMLコンテンツ。
        css_files (Dict[str, str]): CSSファイル名とその内容。
        img_files (Dict[str, str]): 画像ファイルのソースパスと保存先のファイル名。
    """
    # HTMLファイル保存
    with open(os.path.join(base_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)

    # CSSファイル保存
    for file_name, content in css_files.items():
        with open(os.path.join(base_dir, 'css', file_name), 'w', encoding='utf-8') as f:
            f.write(content)

    # 画像ファイル保存
    for src, dest in img_files.items():
        shutil.move(src, os.path.join(base_dir, 'img', dest))

def create_zip(base_dir: str) -> str:
    """
    指定されたディレクトリをZIPファイルとして圧縮します。

    Parameters:
        base_dir (str): 圧縮するディレクトリ。

    Returns:
        str: 圧縮されたZIPファイルのパス。
    """
    zip_file_path = f'{base_dir}.zip'
    with zipfile.ZipFile(zip_file_path, 'w') as zipf:
        for root, _, files in os.walk(base_dir):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, os.path.relpath(file_path, base_dir))
    return zip_file_path

def create_lp_structure(base_dir: str, html_content: str, css_files: Dict[str, str], img_files: Dict[str, str]) -> str:
    """
    LPのファイル構造を作成し、指定されたディレクトリに保存後、ZIPファイルを作成します。

    Parameters:
        base_dir (str): 保存先のベースディレクトリ。
        html_content (str): 生成するHTMLコンテンツ。
        css_files (Dict[str, str]): CSSファイル名とその内容。
        img_files (Dict[str, str]): 画像ファイルのソースパスと保存先のファイル名。

    Returns:
        str: 作成されたZIPファイルのパス。
    """
    try:
        # ディレクトリ構造作成
        create_directory_structure(base_dir, ['css', 'img'])

        # ファイル保存
        save_files(base_dir, html_content, css_files, img_files)

        # ZIPファイル作成
        return create_zip(base_dir)

    except Exception as e:
        print(f"フォルダ構造の作成中にエラーが発生しました: {e}")
        return ""
