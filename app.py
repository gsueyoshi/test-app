import os
import zipfile
import time  # 待機時間を追加するために必要
from flask import Flask, request, jsonify, send_file, render_template
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
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)  # 特殊文字を削除
    text = re.sub(r'[\s_]+', '-', text)  # スペースやアンダースコアをハイフンに変換
    return text.lower()[:max_length]  # ケバブケース化して15文字に制限

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

def generate_images_from_prompts(prompts, n=1, size="1024x1024", batch_size=2, delay=3):
    """バッチ処理で画像を生成し、速度制限を回避"""
    image_urls = []
    for i in range(0, len(prompts), batch_size):
        batch_prompts = prompts[i:i+batch_size]
        try:
            for prompt in batch_prompts:
                response = openai.Image.create(prompt=prompt, n=n, size=size)
                if 'data' not in response or not isinstance(response['data'], list):
                    raise ValueError("Invalid response from OpenAI API.")
                image_urls.extend(data['url'] for data in response['data'])
            
            # バッチ処理後に待機時間を挿入（3秒）
            time.sleep(delay)
        except Exception as e:
            raise Exception(f"Error generating images in batch: {str(e)}")
    return image_urls

def create_zip_and_upload_to_s3(image_urls, frontend_files, company_info):
    """Create a ZIP file from a list of image paths and upload it to S3, ensuring the same file names are used in HTML and the ZIP."""
    zip_buffer = BytesIO()
    image_filenames = []  # 画像ファイル名を保存するリスト
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 生成された画像を追加
            for idx, url in enumerate(image_urls):
                response = requests.get(url)
                img = Image.open(BytesIO(response.content))
                kebab_file_name = convert_to_kebab_case(f"generated_image_{idx+1}")
                image_filenames.append(kebab_file_name + ".png")  # ファイル名を保存
                img_buffer = BytesIO()
                img.save(img_buffer, format="PNG")
                img_buffer.seek(0)
                zf.writestr(f"static/img/{kebab_file_name}.png", img_buffer.getvalue())

            # フロントエンドファイルを追加
            html_content = frontend_files['html']
            css_content = frontend_files['css']
            js_content = frontend_files['js']
            php_content = frontend_files['php']

            # HTML内の画像参照ファイル名を修正
            for idx, filename in enumerate(image_filenames):
                html_content = html_content.replace(f"image_placeholder_{idx+1}.png", filename)

            zf.writestr("index.html", html_content)
            zf.writestr("static/css/styles.css", css_content)
            zf.writestr("static/js/scripts.js", js_content)
            zf.writestr("static/php/functions.php", php_content)

            # 会社情報ファイルを追加
            company_info_str = f"Company Name: {company_info['name']}\nDescription: {company_info['description']}"
            zf.writestr("company_info.txt", company_info_str)

        zip_buffer.seek(0)
        s3_file_name = "project.zip"
        s3.upload_fileobj(zip_buffer, S3_BUCKET_NAME, s3_file_name)

        return f"s3://{S3_BUCKET_NAME}/{s3_file_name}"
    except Exception as e:
        raise Exception(f"Error creating ZIP file: {str(e)}")


@app.route('/', methods=['GET', 'POST'])
def index():
    """Render the index page or handle data submission."""
    if request.method == 'POST':
        data = request.json
        company_info = data.get('company_info', {})
        sections = data.get('sections', [])
        return render_template('index.html', company_info=company_info, sections=sections)

    # デフォルトの会社情報を表示
    default_company_info = {
        'name': 'Default Company',
        'description': 'This is a default description of the company.'
    }
    default_sections = [
        {'title': 'Our Mission', 'content': 'To provide the best services to our customers.'},
        {'title': 'Our Vision', 'content': 'To be the leading provider in our industry.'},
        {'title': 'Our Values', 'content': 'Integrity, Customer Focus, Innovation.'}
    ]
    return render_template('index.html', company_info=default_company_info, sections=default_sections)

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
        # ZIPファイルを作成してS3にアップロード
        s3_url = create_zip_and_upload_to_s3(image_urls, data['frontend_files'], data['company_info'])
        return jsonify({'s3_url': s3_url})
    except Exception as e:
        return jsonify({'error': f"Failed to create ZIP file: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
