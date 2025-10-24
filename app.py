import os
import io
import re
from datetime import datetime
from flask import Flask, render_template_string, request, send_file, session, redirect, url_for, flash

# --- Bibliotecas para processamento de imagem e PDF ---
import pdfplumber      # Para ler PDFs baseados em texto
from PIL import Image  # Para manipular imagens
import pytesseract     # O motor de OCR para ler texto de imagens
from PyPDFForm import PdfWrapper  # Biblioteca para preencher PDFs com campos

# ==============================================================================
# CONFIGURAÇÃO INICIAL - VERIFIQUE ISTO
# ==============================================================================
# No ambiente do Codespaces (baseado em Linux), geralmente não precisamos configurar
# o caminho do Tesseract. Ele já vem pré-instalado ou é fácil de instalar.
# Deixaremos esta parte comentada por enquanto.
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
# ==============================================================================

app = Flask(__name__)
app.config['SECRET_KEY'] = 'uma_chave_secreta_muito_forte_e_dificil_de_adivinhar'

# Nomes dos arquivos e pastas usados pelo app
HEMOBA_TEMPLATE_PATH = 'modelo_hemo.pdf'

# ==============================================================================
# FUNÇÕES DE EXTRAÇÃO DE DADOS (O "CÉREBRO" DO APP)
# ==============================================================================

def extract_field(text, patterns, default=""):
    """Função mais robusta que tenta múltiplos padrões para encontrar um campo."""
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            # Pega o último grupo capturado, que geralmente é o valor
            return match.group(match.lastindex).strip()
    return default

def get_text_from_file(file_stream, filename):
    """Extrai texto bruto de um arquivo, seja PDF ou imagem, de forma inteligente."""
    full_text = ""
    file_extension = os.path.splitext(filename)[1].lower()

    if file_extension == '.pdf':
        try:
            # 1. Tenta ler o PDF como texto digital primeiro (mais preciso)
            file_stream.seek(0)
            with pdfplumber.open(file_stream) as pdf:
                for page in pdf.pages:
                    full_text += page.extract_text(layout=True) or ""

            # 2. Se o texto for muito curto, provavelmente é um PDF de imagem. Força o OCR.
            if len(full_text.strip()) < 200:
                print("PDF com pouco texto, forçando OCR para mais precisão...")
                file_stream.seek(0)
                full_text = "" # Reseta o texto para usar apenas o do OCR
                with pdfplumber.open(file_stream) as pdf:
                    for page in pdf.pages:
                        # Converte a página do PDF em uma imagem de alta qualidade
                        image = page.to_image(resolution=300).original
                        # Extrai texto da imagem usando o OCR
                        full_text += pytesseract.image_to_string(image, lang='por') + '\n'
        except Exception as e:
            print(f"Erro ao processar PDF: {e}")
            return ""

    elif file_extension in ['.png', '.jpg', '.jpeg', '.tiff', '.bmp']:
        # 3. Se for imagem, usa OCR diretamente
        try:
            image = Image.open(file_stream)
            full_text = pytesseract.image_to_string(image, lang='por')
        except Exception as e:
            print(f"Erro ao processar Imagem: {e}")
            return ""
    
    # Salva o texto extraído para você poder verificar o que a IA "leu"
    with open("extracted_text.txt", "w", encoding="utf-8") as f:
        f.write(full_text)
        
    return full_text

def extract_data_from_aih_text(full_text):
    """Recebe o texto bruto e extrai os campos específicos da AIH usando padrões."""
    
    # Padrões de expressões regulares (regex) para encontrar os dados.
    # Eles tentam ser flexíveis com quebras de linha e espaços.
    patterns = {
        'nome_paciente': [r"Nome do Paciente\s*[:\-\s\n]+([A-ZÀ-ÿ\s]+)"],
        'cartao_sus': [r"CNS\s*[:\-\s\n]+(\d{15})"],
        'data_nascimento': [r"Data de Nasc\s*[:\-\s\n]+(\d{2}/\d{2}/\d{4})"],
        'sexo': [r"Sexo\s*[:\-\s\n]+(Masculino|Feminino)"],
        'nome_genitora': [r"Nome da Mãe\s*[:\-\s\n]+([A-ZÀ-ÿ\s]+)"],
        'endereco_completo': [r"Endereço Residencial.+\s*[:\-\s\n]+(.+)"],
        'municipio_origem': [r"Município de Referência\s*[:\-\s\n]+([A-ZÀ-ÿ\s]+)"],
        'hospital': [r"Nome do Estabelecimento Solicitante\s*[:\-\s\n]+(.+)"],
        'prontuario': [r"Núm\. Prontuário\s*[:\-\s\n]+(\d+)"]
    }
    
    data = {}
    for field, field_patterns in patterns.items():
        data[field] = extract_field(full_text, field_patterns)

    # Lógica de limpeza pós-extração
    if data['nome_genitora'] and data['nome_paciente'] in data['nome_genitora']:
        data['nome_genitora'] = data['nome_genitora'].replace(data['nome_paciente'], '').strip()

    # Adiciona campos de data e hora atuais
    data['data'] = datetime.now().strftime('%d/%m/%Y')
    data['hora'] = datetime.now().strftime('%H:%M')
    
    return data

# ==============================================================================
# ROTAS DO SERVIDOR WEB (FLASK) E TEMPLATE HTML
# ==============================================================================

@app.route('/')
def index():
    """Mostra a página inicial."""
    session.pop('form_data', None)
    return render_template_string(HTML_FORM_TEMPLATE, form_data={})

@app.route('/upload', methods=['POST'])
def upload_file():
    """Lida com o upload do arquivo (PDF ou Foto), extrai os dados e mostra o formulário."""
    if 'file' not in request.files or request.files['file'].filename == '':
        flash('Nenhum arquivo selecionado. Por favor, escolha um arquivo.', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    try:
        file_stream = io.BytesIO(file.read())
        raw_text = get_text_from_file(file_stream, file.filename)
        
        if not raw_text or not raw_text.strip():
            flash('Não foi possível extrair texto do arquivo. Verifique se a imagem está nítida ou se o PDF não está em branco.', 'error')
            return redirect(url_for('index'))

        extracted_data = extract_data_from_aih_text(raw_text)
        session['form_data'] = extracted_data
        flash('Dados extraídos! Por favor, revise e complete o formulário abaixo.', 'success')
        
        return render_template_string(HTML_FORM_TEMPLATE, form_data=extracted_data)
    except Exception as e:
        flash(f"Ocorreu um erro inesperado ao processar o arquivo: {e}", "error")
        return redirect(url_for('index'))

@app.route('/submit', methods=['POST'])
def submit_form():
    """Recebe os dados do formulário revisado e gera o PDF final."""
    form_data = request.form.to_dict()
    try:
        if not os.path.exists(HEMOBA_TEMPLATE_PATH):
            flash(f"Erro Crítico: O arquivo modelo '{HEMOBA_TEMPLATE_PATH}' não foi encontrado. Verifique se ele está na mesma pasta do `app.py`.", "error")
            return render_template_string(HTML_FORM_TEMPLATE, form_data=form_data)
        
        # Usa PyPDFForm para preencher o PDF.
        # A partir da versão 3, a classe correta é PdfWrapper. O parâmetro
        # `flatten=True` faz o "achatamento" do PDF, tornando os campos não editáveis.
        wrapper = PdfWrapper(HEMOBA_TEMPLATE_PATH, adobe_mode=False)
        filled_pdf = wrapper.fill(form_data, flatten=True)

        # Define o nome do arquivo de saída de forma segura
        output_filename = f"HEMOBA_preenchido_{form_data.get('nome_paciente', 'paciente')}.pdf"
        # Escreve o PDF preenchido em disco
        filled_pdf.write(output_filename)

        session.pop('form_data', None)

        # Envia o arquivo como resposta para download
        return send_file(
            output_filename,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=output_filename
        )
    except Exception as e:
        flash(f"Ocorreu um erro ao gerar o PDF: {e}. Verifique se o PDF modelo tem campos de formulário corretos.", "error")
        return render_template_string(HTML_FORM_TEMPLATE, form_data=form_data)

# Template HTML com design melhorado e mais campos.
HTML_FORM_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Preenchedor de Ficha HEMOBA</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; padding: 20px; max-width: 800px; margin: auto; background-color: #f4f7f9; }
        .container { background-color: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
        h1, h2 { color: #1a237e; text-align: center; }
        h1 { font-size: 2em; margin-bottom: 10px; }
        h2 { font-size: 1.5em; margin-top: 40px; border-bottom: 2px solid #e0e0e0; padding-bottom: 10px; }
        label { display: block; margin-top: 16px; margin-bottom: 6px; font-weight: 600; color: #333; }
        input[type=text], input[type=date], input[type=time], select { width: 100%; padding: 12px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 5px; transition: border-color 0.2s; }
        input:focus { border-color: #1a237e; outline: none; }
        .prefilled { background-color: #e8f5e9; border-left: 4px solid #4caf50; }
        button[type=submit] { background-color: #1a237e; color: white; padding: 14px 28px; border: none; border-radius: 5px; cursor: pointer; font-size: 1.1em; font-weight: 600; display: block; margin: 40px auto 20px auto; transition: background-color 0.2s; }
        button[type=submit]:hover { background-color: #3f51b5; }
        .upload-section { margin-bottom: 30px; padding: 25px; background-color: #fafafa; border: 2px dashed #ccc; border-radius: 8px; text-align: center; }
        .flash-message { padding: 15px; margin: 20px 0; border-radius: 5px; font-size: 1.1em; text-align: center; }
        .flash-success { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash-error { background-color: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
        .field-group { margin-bottom: 10px; }
        /* Adicionando estilos para checkboxes */
        .checkbox-group { margin-top: 20px; }
        .checkbox-item { display: flex; align-items: center; margin-bottom: 10px; }
        .checkbox-item input[type="checkbox"] { margin-right: 10px; width: auto; }
        .checkbox-item label { margin: 0; font-weight: normal; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Preenchedor Automático - HEMOBA</h1>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message flash-{{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <div class="upload-section">
            <h2>1. Enviar Ficha AIH (PDF ou Foto)</h2>
            <form action="{{ url_for('upload_file') }}" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".pdf,.png,.jpg,.jpeg" required>
                <button type="submit" style="background-color: #d32f2f; margin-top: 20px;">Extrair Dados</button>
            </form>
        </div>

        <form method="post" action="{{ url_for('submit_form') }}">
            <h2>2. Revisar e Completar Dados</h2>
            
            <h3>Identificação</h3>
            <div class="grid">
                <div class="field-group"><label for="hospital">Hospital / Unidade</label><input type="text" name="hospital" class="{{ 'prefilled' if form_data.get('hospital') else '' }}" value="{{ form_data.get('hospital', '') }}"></div>
                <div class="field-group"><label for="nome_paciente">Nome do Paciente</label><input type="text" name="nome_paciente" class="{{ 'prefilled' if form_data.get('nome_paciente') else '' }}" value="{{ form_data.get('nome_paciente', '') }}"></div>
                <div class="field-group"><label for="nome_genitora">Nome da Mãe</label><input type="text" name="nome_genitora" class="{{ 'prefilled' if form_data.get('nome_genitora') else '' }}" value="{{ form_data.get('nome_genitora', '') }}"></div>
                <div class="field-group"><label for="data_nascimento">Data de Nascimento (dd/mm/aaaa)</label><input type="text" name="data_nascimento" class="{{ 'prefilled' if form_data.get('data_nascimento') else '' }}" placeholder="dd/mm/aaaa" value="{{ form_data.get('data_nascimento', '') }}"></div>
                <div class="field-group"><label for="cartao_sus">Cartão SUS (CNS)</label><input type="text" name="cartao_sus" class="{{ 'prefilled' if form_data.get('cartao_sus') else '' }}" value="{{ form_data.get('cartao_sus', '') }}"></div>
                <div class="field-group"><label for="prontuario">Prontuário</label><input type="text" name="prontuario" class="{{ 'prefilled' if form_data.get('prontuario') else '' }}" value="{{ form_data.get('prontuario', '') }}"></div>
                <div class="field-group"><label for="sexo">Sexo</label><input type="text" name="sexo" class="{{ 'prefilled' if form_data.get('sexo') else '' }}" value="{{ form_data.get('sexo', '') }}"></div>
                <div class="field-group"><label for="data">Data</label><input type="text" name="data" placeholder="dd/mm/aaaa" value="{{ form_data.get('data', '') }}"></div>
                <div class="field-group"><label for="hora">Hora</label><input type="text" name="hora" placeholder="HH:MM" value="{{ form_data.get('hora', '') }}"></div>
            </div>
            
            <h3>Endereço</h3>
            <div class="grid">
                <div class="field-group" style="grid-column: 1 / -1;"><label for="endereco_residencia">Endereço Completo</label><input type="text" name="endereco_residencia" class="{{ 'prefilled' if form_data.get('endereco_completo') else '' }}" value="{{ form_data.get('endereco_completo', '') }}"></div>
                <div class="field-group"><label for="municipio_origem">Município</label><input type="text" name="municipio_origem" class="{{ 'prefilled' if form_data.get('municipio_origem') else '' }}" value="{{ form_data.get('municipio_origem', '') }}"></div>
            </div>

            <h3>Dados Clínicos (Preenchimento Manual)</h3>
            <div class="grid">
                 <div class="field-group"><label for="diagnostico">Diagnóstico</label><input type="text" name="diagnostico" value=""></div>
                 <div class="field-group"><label for="indicacao_transfusional">Indicação Transfusional</label><input type="text" name="indicacao_transfusional" value=""></div>
            </div>

            <h3>Hemoterapia</h3>
            <div class="checkbox-group">
                <div class="checkbox-item"><input type="checkbox" id="produto_hema" name="produto_hema" value="On"><label for="produto_hema">Concentrado de Hemácias</label></div>
                <div class="checkbox-item"><input type="checkbox" id="produto_pfc" name="produto_pfc" value="On"><label for="produto_pfc">Plasma Fresco</label></div>
                <div class="checkbox-item"><input type="checkbox" id="produto_plaquetas" name="produto_plaquetas" value="On"><label for="produto_plaquetas">Concentrado de Plaquetas</label></div>
                <div class="checkbox-item"><input type="checkbox" id="produto_crio" name="produto_crio" value="On"><label for="produto_crio">Crioprecipitado</label></div>
            </div>
            
            <button type="submit">Gerar PDF Final</button>
        </form>
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)
