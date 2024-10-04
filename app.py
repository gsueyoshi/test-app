import os
import zipfile
import time
from flask import Flask, request, jsonify, send_file
from io import BytesIO
from PIL import Image
import boto3
import re
import requests
import openai
from celery import Celery
from marshmallow import Schema, fields, ValidationError

app = Flask(__name__)

# Celeryの設定
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# OpenAI APIキー設定
openai.api_key = os.getenv('OPENAI_API_KEY')
if not openai.api_key:
    raise EnvironmentError("OPENAI_API_KEY is not set in environment variables.")

# S3設定
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS'),
    region_name=os.getenv('AWS_REGION')
)
S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')

def convert_to_kebab_case(text, max_length=15):
    """文字列をケバブケースに変換して最大長を15文字に制限"""
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)  # 特殊文字を削除
    text = re.sub(r'[\s_]+', '-', text)  # スペースやアンダースコアをハイフンに変換
    return text.lower()[:max_length]

class FrontendFilesSchema(Schema):
    html = fields.Str(required=True)
    css = fields.Str(required=True)
    js = fields.Str(required=True)
    php = fields.Str(required=True)

class CompanyInfoSchema(Schema):
    name = fields.Str(required=True)
    description = fields.Str(required=True)

class GenerateImagesSchema(Schema):
    prompts = fields.List(fields.Str(), required=True, validate=lambda p: len(p) <= 10)
    external_server_url = fields.Str(required=True)
    n_images = fields.Int(required=False, validate=lambda n: 0 <= n <= 10, default=1)
    frontend_files = fields.Nested(FrontendFilesSchema, required=True)
    company_info = fields.Nested(CompanyInfoSchema, required=True)

@celery.task
def generate_images_task(prompts, n_images):
    """非同期で画像を生成するためのタスク"""
    return generate_images_from_prompts(prompts, n=n_images)

def generate_images_from_prompts(prompts, n=1, size="1024x1024", batch_size=1, delay=10, timeout=120, retries=3):
    """バッチ処理で画像を生成し、タイムアウトとリトライを設定"""
    image_urls = []
    for i in range(0, len(prompts), batch_size):
        batch_prompts = prompts[i:i + batch_size]
        for attempt in range(retries):
            try:
                for prompt in batch_prompts:
                    response = openai.Image.create(prompt=prompt, n=n, size=size, timeout=timeout)
                    if 'data' not in response or not isinstance(response['data'], list):
                        raise ValueError("Invalid response from OpenAI API.")
                    image_urls.extend(data['url'] for data in response['data'])
                break  # 成功したらループを抜ける
            except Exception as e:
                print(f"Error: {e}, Retrying... {attempt+1}/{retries}")
                time.sleep(delay * (attempt + 1))  # エラー発生時にバックオフで待機
        else:
            raise Exception("Max retries reached. Failed to generate images.")
        time.sleep(delay)  # バッチ処理後の待機時間
    return image_urls

def save_images_to_mnt(image_urls):
    """Saves images from URLs to the /mnt folder and returns local file paths."""
    file_paths = []
    mnt_folder = '/mnt/'
    
    if not os.path.exists(mnt_folder):
        os.makedirs(mnt_folder)
    
    for idx, url in enumerate(image_urls):
        response = requests.get(url)
        img = Image.open(BytesIO(response.content))
        file_path = os.path.join(mnt_folder, f"generated_image_{idx+1}.png")
        img.save(file_path, format="PNG")
        file_paths.append(file_path)
    
    return file_paths

def create_zip_and_upload_to_s3(file_paths, frontend_files, company_info):
    """画像とフロントエンドファイルをまとめてZIPにし、S3にアップロード"""
    zip_buffer = BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 生成された画像を追加
            for file_path in file_paths:
                with open(file_path, "rb") as img_file:
                    zf.writestr(f"static/img/{os.path.basename(file_path)}", img_file.read())

            # フロントエンドファイルを追加
            zf.writestr("index.html", frontend_files['html'])
            zf.writestr("static/css/styles.css", frontend_files['css'])
            zf.writestr("static/js/scripts.js", frontend_files['js'])
            zf.writestr("static/php/functions.php", frontend_files['php'])

            # 会社情報ファイルを追加
            company_info_str = f"Company Name: {company_info['name']}\nDescription: {company_info['description']}"
            zf.writestr("company_info.txt", company_info_str)

        zip_buffer.seek(0)
        s3_file_name = "project.zip"
        s3.upload_fileobj(zip_buffer, S3_BUCKET_NAME, s3_file_name)

        return f"s3://{S3_BUCKET_NAME}/{s3_file_name}"
    except Exception as e:
        raise Exception(f"Error creating ZIP file: {str(e)}")

@app.route('/generate_images', methods=['POST'])
def generate_images():
    json_data = request.get_json()
    
    # バリデーション
    try:
        data = GenerateImagesSchema().load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 400

    # 画像生成処理の非同期実行
    task = generate_images_task.delay(data['prompts'], data['n_images'])
    return jsonify({"task_id": task.id, "status": "Image generation in progress."}), 202

@app.route('/create_zip', methods=['POST'])
def create_zip():
    json_data = request.get_json()

    # バリデーション
    try:
        data = GenerateImagesSchema().load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 400

    try:
        # 画像生成
        image_urls = generate_images_from_prompts(data['prompts'], n=data['n_images'])

        # 画像を /mnt/ に保存
        file_paths = save_images_to_mnt(image_urls)

        # ZIPファイルを作成してS3にアップロード
        s3_url = create_zip_and_upload_to_s3(file_paths, data['frontend_files'], data['company_info'])
        return jsonify({'s3_url': s3_url})
    except Exception as e:
        return jsonify({'error': f"Failed to create ZIP file: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
