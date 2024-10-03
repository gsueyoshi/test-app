import os
import zipfile
from flask import Flask, request, jsonify
from io import BytesIO
from PIL import Image
import boto3
from botocore.exceptions import NoCredentialsError
import re
import requests
import openai  # OpenAIのライブラリをインポート

app = Flask(__name__)

# OpenAI APIキーの設定
openai.api_key = os.getenv('OPENAI_API_KEY')

# S3クライアントの設定
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS'),
    region_name=os.getenv('AWS_REGION')
)
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

# ファイル名をケバブケースに変換し、20文字以内に制限する関数
def convert_to_kebab_case(text, max_length=20):
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)  # 記号などを削除
    text = re.sub(r'[\s_]+', '-', text)  # 空白やアンダースコアをハイフンに変換
    return text.lower()[:max_length]  # 20文字以内でケバブケース化

# DALL·Eで画像を生成する関数
def generate_images_from_prompts(prompts, n=1, size="1024x1024"):
    image_urls = []
    try:
        for prompt in prompts:
            response = openai.Image.create(
                prompt=prompt,
                n=n,  # 各プロンプトで生成する画像の数
                size=size
            )
            for data in response['data']:
                image_urls.append(data['url'])
        return image_urls
    except Exception as e:
        raise Exception(f"Error generating images: {str(e)}")

# 画像をダウンロードまたはローカルから読み込み、PNG形式に変換してメモリに保存する関数
def download_or_read_image(image_path, file_name):
    try:
        if image_path.startswith(('http://', 'https://')):
            # URLの場合、画像をダウンロード
            response = requests.get(image_path)
            response.raise_for_status()  # エラーハンドリング
            img = Image.open(BytesIO(response.content))
        else:
            # ローカルファイルの場合、画像を開く
            img = Image.open(image_path)

        img_converted = img.convert("RGBA")  # 必要ならRGBAに変換

        # ファイル名をケバブケースに変換
        kebab_file_name = convert_to_kebab_case(file_name)

        # 画像をメモリ上のバッファに保存
        img_buffer = BytesIO()
        img_converted.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        return kebab_file_name, img_buffer
    except Exception as e:
        raise Exception(f"Failed to download or process image: {str(e)}")

# ZIPファイルを作成してS3にアップロードする関数
def create_zip_and_upload_to_s3(image_paths):
    if len(image_paths) < 1 or len(image_paths) > 10:
        raise ValueError("The number of images should be between 1 and 10.")

    zip_buffer = BytesIO()
    try:
        # ZIPファイルをメモリ上に作成
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, image_path in enumerate(image_paths):
                image_name = f"image_{idx+1}"
                kebab_file_name, image_buffer = download_or_read_image(image_path, image_name)
                zf.writestr(f"{kebab_file_name}.png", image_buffer.getvalue())

        # ZIPファイルをメモリ上に保存しS3にアップロード
        zip_buffer.seek(0)
        s3_file_name = "images.zip"
        s3.upload_fileobj(zip_buffer, S3_BUCKET_NAME, s3_file_name)

        return f"s3://{S3_BUCKET_NAME}/{s3_file_name}"
    except Exception as e:
        raise Exception(f"Error creating ZIP file: {str(e)}")

# ルートエンドポイント
@app.route('/')
def index():
    return "Hello, this is the root page!"

# ZIP作成API
@app.route('/create_zip', methods=['POST'])
def create_zip_from_prompts():
    data = request.json
    prompts = data.get('prompts', [])
    n_images = data.get('n_images', 1)
    size = data.get('size', '1024x1024')  # サイズ指定があればそれを使用し、なければデフォルトで1024x1024

    if not prompts or not isinstance(prompts, list):
        return jsonify({'error': 'No prompts provided or invalid format'}), 400

    try:
        # プロンプトで画像を生成（指定サイズを使用）
        image_urls = generate_images_from_prompts(prompts, n=n_images, size=size)

        # 生成された画像をダウンロードしてZIPにまとめ、S3に保存
        s3_url = create_zip_and_upload_to_s3(image_urls)
        return jsonify({'s3_url': s3_url})

    except Exception as e:
        return jsonify({'error': f"Failed to create ZIP file: {str(e)}"}), 500

# Flaskアプリケーションのエントリーポイント
if __name__ == '__main__':
    app.run(debug=True)
