from flask import Flask, render_template, request, send_file, jsonify
import zipfile
import os
import io
import tempfile
import shutil
from imos_import import imos_bp
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max for multiple files
app.register_blueprint(imos_bp)

UPLOAD_FOLDER = tempfile.mkdtemp()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/preview', methods=['POST'])
def preview():
    files = request.files.getlist('file')
    keyword = request.form.get('keyword', 'odoo').strip()

    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        all_results = []
        for file in files:
            if not file.filename:
                continue
            filename = file.filename.lower()
            if filename.endswith('.zip'):
                result = preview_zip(file, keyword)
                all_results.extend(result)
            elif filename.endswith('.xml'):
                result = preview_xml_data(file, keyword)
                all_results.append(result)
            else:
                return jsonify({'error': f'Only .xml and .zip files are supported (got: {file.filename})'}), 400

        return jsonify({'type': 'multi', 'keyword': keyword, 'files': all_results})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def preview_zip(file, keyword):
    results = []
    file_bytes = file.read()
    zip_name = file.filename
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        xml_files = [n for n in zf.namelist() if n.lower().endswith('.xml')]
        if not xml_files:
            return []
        for xml_name in xml_files:
            with zf.open(xml_name) as xf:
                lines = xf.read().decode('utf-8', errors='replace').splitlines(keepends=True)
            matched = sum(1 for l in lines if keyword.lower() in l.lower())
            results.append({
                'filename': xml_name,
                'zip_source': zip_name,
                'total_lines': len(lines),
                'matched_lines': matched,
                'sample': get_sample_lines(lines, keyword)
            })
    return results


def preview_xml_data(file, keyword):
    lines = file.read().decode('utf-8', errors='replace').splitlines(keepends=True)
    matched = sum(1 for l in lines if keyword.lower() in l.lower())
    return {
        'filename': file.filename,
        'zip_source': None,
        'total_lines': len(lines),
        'matched_lines': matched,
        'sample': get_sample_lines(lines, keyword)
    }


def get_sample_lines(lines, keyword, max_samples=5):
    samples = []
    for i, line in enumerate(lines):
        if keyword.lower() in line.lower():
            samples.append({'line_number': i + 1, 'content': line.rstrip()})
            if len(samples) >= max_samples:
                break
    return samples


@app.route('/process', methods=['POST'])
def process():
    files = request.files.getlist('file')
    keyword = request.form.get('keyword', 'odoo').strip()

    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No file uploaded'}), 400

    try:
        valid_files = [f for f in files if f.filename]

        # Single XML file
        if len(valid_files) == 1 and valid_files[0].filename.lower().endswith('.xml'):
            return process_xml(valid_files[0], keyword)

        # Single ZIP file
        if len(valid_files) == 1 and valid_files[0].filename.lower().endswith('.zip'):
            return process_zip_single(valid_files[0], keyword)

        # Multiple files -> merge into one output ZIP
        return process_multiple(valid_files, keyword)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def process_zip_single(file, keyword):
    file_bytes = file.read()
    output_buffer = io.BytesIO()

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf_in:
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf_out:
            for item in zf_in.infolist():
                data = zf_in.read(item.filename)
                if item.filename.lower().endswith('.xml'):
                    text = data.decode('utf-8', errors='replace')
                    lines = text.splitlines(keepends=True)
                    filtered = [l for l in lines if keyword.lower() not in l.lower()]
                    data = ''.join(filtered).encode('utf-8')
                zf_out.writestr(item, data)

    output_buffer.seek(0)
    out_name = file.filename.rsplit('.', 1)[0] + '_cleaned.zip'
    return send_file(
        output_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=out_name
    )


def process_multiple(files, keyword):
    """Process multiple ZIP/XML files and bundle all cleaned outputs into one ZIP."""
    output_buffer = io.BytesIO()

    with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zf_out:
        for file in files:
            filename = file.filename.lower()
            if filename.endswith('.zip'):
                file_bytes = file.read()
                base_name = file.filename.rsplit('.', 1)[0]
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf_in:
                    for item in zf_in.infolist():
                        data = zf_in.read(item.filename)
                        if item.filename.lower().endswith('.xml'):
                            text = data.decode('utf-8', errors='replace')
                            lines = text.splitlines(keepends=True)
                            filtered = [l for l in lines if keyword.lower() not in l.lower()]
                            data = ''.join(filtered).encode('utf-8')
                        # Nest inside a folder named after the source ZIP
                        out_path = f"{base_name}_cleaned/{item.filename}"
                        zf_out.writestr(out_path, data)

            elif filename.endswith('.xml'):
                lines = file.read().decode('utf-8', errors='replace').splitlines(keepends=True)
                filtered = [l for l in lines if keyword.lower() not in l.lower()]
                out_name = file.filename.rsplit('.', 1)[0] + '_cleaned.xml'
                zf_out.writestr(out_name, ''.join(filtered).encode('utf-8'))

    output_buffer.seek(0)
    return send_file(
        output_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name='all_cleaned_files.zip'
    )


def process_xml(file, keyword):
    lines = file.read().decode('utf-8', errors='replace').splitlines(keepends=True)
    filtered = [l for l in lines if keyword.lower() not in l.lower()]
    output_buffer = io.BytesIO(''.join(filtered).encode('utf-8'))
    output_buffer.seek(0)
    out_name = file.filename.rsplit('.', 1)[0] + '_cleaned.xml'
    return send_file(
        output_buffer,
        mimetype='application/xml',
        as_attachment=True,
        download_name=out_name
    )


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5001, debug=True)