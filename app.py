import os
import zipfile
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
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'  # Redisを使用
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# 環境変数から設定
openai.api_key = os.getenv('OPENAI_API_KEY')
if not openai.api_key:
    raise EnvironmentError("OPENAI_API_KEY is not set in environment variables.")

# S3クライアントの設定
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS'),
    region_name=os.getenv('AWS_REGION')
)
if not all([os.getenv('AWS_ACCESS_KEY_ID'), os.getenv('AWS_SECRET_ACCESS'), os.getenv('AWS_REGION')]):
    raise EnvironmentError("AWS credentials are not set in environment variables.")

S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
if not S3_BUCKET_NAME:
    raise EnvironmentError("S3_BUCKET_NAME is not set in environment variables.")

def convert_to_kebab_case(text, max_length=15):
    """Convert a given text to kebab-case with a maximum length."""
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text)  # Remove special characters
    text = re.sub(r'[\s_]+', '-', text)  # Replace spaces and underscores with hyphens
    return text.lower()[:max_length]  # Limit to 15 characters

# スキーマの定義
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
    """Background task to generate images from prompts."""
    return generate_images_from_prompts(prompts, n=n_images)

def generate_images_from_prompts(prompts, n=1, size="1024x1024"):
    """Generate images from a list of prompts using OpenAI's DALL·E."""
    image_urls = []
    try:
        for prompt in prompts:
            response = openai.Image.create(prompt=prompt, n=n, size=size)
            if 'data' not in response or not isinstance(response['data'], list):
                raise ValueError("Invalid response from OpenAI API.")
            image_urls.extend(data['url'] for data in response['data'])
        return image_urls
    except Exception as e:
        raise Exception(f"Error generating images: {str(e)}")

def download_or_read_image(image_path):
    """Download or read an image from a local path or URL, convert to PNG format."""
    try:
        if image_path.startswith(('http://', 'https://')):
            response = requests.get(image_path)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(image_path)

        img_converted = img.convert("RGBA")
        return img_converted
    except Exception as e:
        raise Exception(f"Failed to download or process image: {str(e)}")

def create_zip_and_upload_to_s3(image_paths, uploaded_images):
    """Create a ZIP file from a list of image paths and upload it to S3."""
    total_images = len(image_paths) + len(uploaded_images)
    if total_images < 0 or total_images > 10:
        raise ValueError("The total number of images (generated + uploaded) should be between 0 and 10.")

    zip_buffer = BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Process generated images
            for idx, image_path in enumerate(image_paths):
                img_converted = download_or_read_image(image_path)
                kebab_file_name = convert_to_kebab_case(f"generated_image_{idx + 1}")
                img_buffer = BytesIO()
                img_converted.save(img_buffer, format="PNG")
                img_buffer.seek(0)
                zf.writestr(f"static/img/{kebab_file_name}.png", img_buffer.getvalue())

            # Process uploaded images
            for uploaded_image in uploaded_images:
                filename = convert_to_kebab_case(uploaded_image.filename)
                img_buffer = BytesIO(uploaded_image.read())
                zf.writestr(f"static/img/{filename}", img_buffer.getvalue())

        zip_buffer.seek(0)
        s3_file_name = "images.zip"
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
    """API endpoint to generate images from prompts."""
    json_data = request.get_json()

    # バリデーション
    try:
        data = GenerateImagesSchema().load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 400

    # 非同期に画像生成を実行
    task = generate_images_task.delay(data['prompts'], data['n_images'])
    return jsonify({"task_id": task.id, "status": "Image generation in progress."}), 202

@app.route('/create_zip', methods=['POST'])
def create_zip_from_prompts():
    """API endpoint to create a ZIP file from image prompts."""
    json_data = request.json

    # バリデーション
    try:
        data = GenerateImagesSchema().load(json_data)
    except ValidationError as err:
        return jsonify(err.messages), 400

    try:
        image_urls = generate_images_from_prompts(data['prompts'], n=data['n_images'])
        s3_url = create_zip_and_upload_to_s3(image_urls, request.files.getlist('uploaded_images'))
        return jsonify({'s3_url': s3_url})
    except Exception as e:
        return jsonify({'error': f"Failed to create ZIP file: {str(e)}"}), 500

@app.route('/download_project', methods=['GET'])
def download_project():
    """API endpoint to download the project structure as a ZIP file."""
    zip_buffer = BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Add static reset.css
            zf.writestr("static/css/reset.css", open('static/css/reset.css').read())
            # Add the generated styles.css and index.html
            zf.writestr("static/css/styles.css", open('static/css/styles.css').read())
            zf.writestr("index.html", open('index.html').read())  # フロントエンドからのindex.htmlを使用
            # Create empty directories for PHP and JS
            zf.writestr("static/js/.gitkeep", "")  # Placeholder for the JS folder
            zf.writestr("static/php/.gitkeep", "")  # Placeholder for the PHP folder
            zf.writestr("static/img/placeholder.png", "Placeholder for images")  # Example placeholder

        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name="project_structure.zip")
    except Exception as e:
        return jsonify({'error': f"Failed to create project ZIP file: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
