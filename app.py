from flask import Flask, request, jsonify, send_file, render_template
from lp_generator import create_lp
import os

app = Flask(__name__)

# 共通のエラーハンドリング
def handle_errors(f):
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except KeyError as e:
            return jsonify({'error': f"欠けているキー: {e}"}), 400
        except Exception as e:
            return jsonify({'error': str(e)}), 500  # 500エラーに変更
    wrapper.__name__ = f.__name__
    return wrapper

@app.route('/')
def home():
    """ルートエンドポイント"""
    return "Welcome to the ChatGPT to Image and LP Generation API!"

@app.route('/generate_lp', methods=['POST'])
@handle_errors
def generate_lp_endpoint():
    """LPを生成するエンドポイント"""
    data = request.get_json()

    # 必要なデータを取得
    company_info = data.get('company_info')
    sections = data.get('sections')
    image_prompts = data.get('image_prompts')
    css_config = data.get('css_config')
    base_dir = '/tmp/lp_structure'

    # LP生成
    zip_file_path = create_lp(base_dir, company_info, sections, image_prompts, css_config)

    if not zip_file_path:
        return jsonify({'error': 'LP作成に失敗しました。'}), 500

    return send_file(zip_file_path, as_attachment=True)

@app.route('/submit_json', methods=['POST'])
@handle_errors
def submit_json():
    """JSONデータを受け取り、会社情報を作成するエンドポイント"""
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
