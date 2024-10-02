from flask import Flask, request, jsonify, send_file
from lp_generator import create_lp
import os

app = Flask(__name__)

@app.route('/generate_lp', methods=['POST'])
def generate_lp_endpoint():
    try:
        data = request.get_json()

        # 必要なデータを取得
        company_info = data['company_info']
        sections = data['sections']
        image_prompts = data['image_prompts']
        css_config = data['css_config']

        # Render の /tmp フォルダに一時的に保存
        base_dir = '/tmp/lp_structure'

        # LP生成
        zip_file_path = create_lp(base_dir, company_info, sections, image_prompts, css_config)

        if not zip_file_path:
            return jsonify({'error': 'LP作成に失敗しました。'}), 500

        # 生成された ZIP ファイルを返す
        return send_file(zip_file_path, as_attachment=True)

    except KeyError as e:
        return jsonify({'error': f"欠けているキー: {e}"}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400


if __name__ == '__main__':
    app.run(debug=True)
