import os
import zipfile
from flask import Flask, request, jsonify, send_file, render_template
from io import BytesIO
from PIL import Image
import boto3
import re
import requests
import openai

app = Flask(__name__)

# OpenAI APIキーの設定
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

def generate_images_from_prompts(prompts, n=1, size="1024x1024"):
    """Generate images from a list of prompts using OpenAI's DALL·E."""
    image_urls = []
    try:
        for prompt in prompts:
            response = openai.Image.create(
                prompt=prompt,
                n=n,
                size=size
            )
            if 'data' not in response or not isinstance(response['data'], list):
                raise ValueError("Invalid response from OpenAI API.")
            for data in response['data']:
                image_urls.append(data['url'])
        return image_urls
    except Exception as e:
        raise Exception(f"Error generating images: {str(e)}")

def download_or_read_image(image_path, file_name):
    """Download or read an image from a local path or URL, convert to PNG format, and return the kebab filename and image buffer."""
    try:
        if image_path.startswith(('http://', 'https://')):
            response = requests.get(image_path)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(image_path)

        img_converted = img.convert("RGBA")
        kebab_file_name = convert_to_kebab_case(file_name)
        img_buffer = BytesIO()
        img_converted.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        return kebab_file_name, img_buffer
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
                image_name = f"generated_image_{idx + 1}"
                kebab_file_name, image_buffer = download_or_read_image(image_path, image_name)
                zf.writestr(f"static/img/{kebab_file_name}.png", image_buffer.getvalue())

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

@app.route('/')
def index():
    """Render the index page."""
    return render_template('index.html')

@app.route('/create_zip', methods=['POST'])
def create_zip_from_prompts():
    """API endpoint to create a ZIP file from image prompts."""
    data = request.json
    prompts = data.get('prompts', [])
    n_images = data.get('n_images', 1)
    size = data.get('size', '1024x1024')
    uploaded_images = request.files.getlist('uploaded_images')

    if not prompts or not isinstance(prompts, list):
        return jsonify({'error': 'No prompts provided or invalid format'}), 400

    try:
        image_urls = generate_images_from_prompts(prompts, n=n_images, size=size)
        s3_url = create_zip_and_upload_to_s3(image_urls, uploaded_images)
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
            zf.writestr("templates/index.html", open('templates/index.html').read())
            zf.writestr("static/img/placeholder.png", "Placeholder for images")  # Example placeholder

        zip_buffer.seek(0)
        return send_file(zip_buffer, as_attachment=True, download_name="project_structure.zip")
    except Exception as e:
        return jsonify({'error': f"Failed to create project ZIP file: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
