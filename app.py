import os
import json
from PIL import Image
from io import BytesIO
import requests
import zipfile
import re
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# ケバブケース変換関数
def convert_to_kebab_case(text, max_length=20):
    kebab_case_text = re.sub(r'\s+', '-', text.lower())
    kebab_case_text = re.sub(r'[^a-z0-9\-]', '', kebab_case_text)  # 英数字とハイフンのみ許可
    return kebab_case_text[:max_length]  # 20文字以内に切り捨て

@app.route('/generate-image', methods=['POST'])
def generate_image():
    data = request.json
    prompt = data['prompt']
    
    # アスペクト比とピクセルサイズの処理
    size = data.get('size')
    aspect_ratio = data.get('aspect_ratio')
    
    # デフォルトの幅を設定（例：1024ピクセル）
    default_width = 1024
    
    if aspect_ratio:
        # アスペクト比を解析して幅と高さを計算
        width_ratio, height_ratio = map(float, aspect_ratio.split(':'))
        height = int(default_width * (height_ratio / width_ratio))
        size = f"{default_width}x{height}"
    elif not size:
        # サイズが指定されていない場合、デフォルトサイズを使用
        size = '1024x1024'
    
    # 生成する画像の枚数を取得（デフォルト1、最大10）
    n = data.get('n', 1)
    if n > 10:
        n = 10  # 最大10枚まで生成可能
    
    # DALL·E APIを呼び出して複数のWebP形式の画像を生成
    api_key = 'YOUR_DALLE_API_KEY'  # OpenAIから取得したAPIキー
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    response = requests.post(
        'https://api.dalle.openai.com/v1/images/generate',
        headers=headers,
        json={'prompt': prompt, 'n': n, 'size': size}
    )
    
    # 生成された画像のURLリストを取得
    data = response.json()
    image_urls = [item['url'] for item in data['data']]
    
    # プロンプトからケバブケースのファイル名を生成（20文字以内）
    base_filename = convert_to_kebab_case(prompt)
    
    # 画像をPNGに変換してZIPにまとめる
    zip_filename = f"{base_filename}.zip"
    zip_path = os.path.join("/tmp", zip_filename)  # /tmpに一時的に保存
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for i, url in enumerate(image_urls):
            webp_image = requests.get(url)
            img_webp = Image.open(BytesIO(webp_image.content))
            png_buffer = BytesIO()
            img_webp.save(png_buffer, format="PNG")
            png_buffer.seek(0)
            # 各画像をPNGファイルとしてZIPに書き込む
            filename = f'{base_filename}-{i+1}.png'  # ケバブケース名 + インデックス
            zipf.writestr(filename, png_buffer.getvalue())
    
    return jsonify({'zip_url': f'/download/{zip_filename}'})

@app.route('/download/<filename>', methods=['GET'])
def download_file(filename):
    file_path = os.path.join("/tmp", filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
