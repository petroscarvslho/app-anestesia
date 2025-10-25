import io
import re
from datetime import datetime, date, time
from collections import defaultdict

import streamlit as st
import fitz  # PyMuPDF
import traceback

# ----------------------------------------------------------
# CONFIG GERAL (mobile-first) + CSS
# ----------------------------------------------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")
MOBILE_CSS = """
<style>
.block-container {max-width: 740px !important; padding-top: 1.2rem;}
h1,h2 { letter-spacing: -0.3px; }
h3 { margin-top: 1.2rem; }
.badge {display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem; padding:.15rem .5rem; border-radius:999px; background:#eef2ff; color:#274 ; border:1px solid #dbeafe;}
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih {background:#3b82f6;}
.dot-ocr {background:#22c55e;}
.dot-man {background:#cbd5e1;}
.stTextInput > div > div > input,
.stTextArea textarea { font-size: 16px !important; }
label {font-weight:600}
hr { border:none; height:1px; background:#eee; margin: 1.2rem 0;}
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# ----------------------------------------------------------
# CONSTANTES
# ----------------------------------------------------------
HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# ----------------------------------------------------------
# HELPERS DE LIMPEZA
# ----------------------------------------------------------
def limpar_nome(txt: str) -> str:
    if not txt:
        return ""
    partes = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]+", txt)
    val = " ".join(partes).strip()
    val = re.sub(r"\s+", " ", val)
    return val

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

def normaliza_data(txt: str) -> str:
    if not txt:
        return ""
    m = re.search(r"(\d{2})[^\d]?(\d{2})[^\d]?(\d{4})", txt)
    if not m:
        return ""
    d, mth, y = m.groups()
    return f"{d}/{mth}/{y}"

def normaliza_telefone(txt: str) -> str:
    if not txt:
        return ""
    dig = so_digitos(txt)
    if len(dig) == 11:
        return f"({dig[:2]}) {dig[2:7]}-{dig[7:]}"
    elif len(dig) == 10:
        return f"({dig[:2]}) {dig[2:6]}-{dig[6:]}"
    return txt

# ----------------------------------------------------------
# EXTRAÇÃO COM PARSER TABULAR
# ----------------------------------------------------------
def extract_pdf_with_words(pdf_bytes: bytes):
    """Extrai palavras com posições do PDF"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    words = page.get_text("words")
    
    # Organizar por linha
    lines = defaultdict(list)
    tolerance = 5
    
    for word in words:
        x0, y0, x1, y1, text, block_no, line_no, word_no = word
        
        found_line = False
        for y_key in list(lines.keys()):
            if abs(y_key - y0) < tolerance:
                lines[y_key].append({
                    "text": text,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1
                })
                found_line = True
                break
        
        if not found_line:
            lines[y0] = [{
                "text": text,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1
            }]
    
    # Ordenar
    sorted_lines = []
    for y in sorted(lines.keys()):
        line_words = sorted(lines[y], key=lambda w: w["x0"])
        sorted_lines.append({
            "y": y,
            "words": line_words,
            "text": " ".join([w["text"] for w in line_words])
        })
    
    return sorted_lines

def match_value_to_label_by_position(label_words, value_line_words, label_text):
    """
    Encontra o valor que corresponde a um rótulo baseado em posição X
    """
    if not label_words or not value_line_words:
        return ""
    
    # Calcular posição X média do rótulo
    label_x_start = label_words[0]["x0"]
    label_x_end = label_words[-1]["x1"]
    label_x_center = (label_x_start + label_x_end) / 2
    
    # Encontrar palavras da linha de valor que estão alinhadas com o rótulo
    aligned_words = []
    tolerance = 80  # pixels de tolerância
    
    for word in value_line_words:
        word_x_center = (word["x0"] + word["x1"]) / 2
        
        # Verificar se está alinhado
        if abs(word_x_center - label_x_center) < tolerance:
            aligned_words.append(word)
        # Ou se começa próximo ao início do rótulo
        elif abs(word["x0"] - label_x_start) < tolerance:
            aligned_words.append(word)
    
    # Se não encontrou alinhado, pegar palavras que começam após o rótulo
    if not aligned_words:
        for word in value_line_words:
            if word["x0"] >= label_x_start - 20:  # pequena margem
                aligned_words.append(word)
    
    # Ordenar por posição X e juntar
    aligned_words.sort(key=lambda w: w["x0"])
    
    # Limitar ao próximo rótulo (não pegar valores de outras colunas)
    result_words = []
    last_word_obj = None
    for word in aligned_words:
        # Parar se encontrar palavra que parece ser outro campo
        if last_word_obj and word["x0"] > last_word_obj["x1"] + 100:
            break
        result_words.append(word["text"])
        last_word_obj = word
    
    return " ".join(result_words)

def parse_aih_tabular(pdf_bytes: bytes):
    """Parser inteligente que entende layout tabular"""
    lines = extract_pdf_with_words(pdf_bytes)
    
    data = {
        "nome_paciente": "",
        "nome_genitora": "",
        "cartao_sus": "",
        "data_nascimento": "",
        "sexo": "",
        "raca": "",
        "telefone_paciente": "",
        "prontuario": "",
        "endereco_completo": "",
        "municipio_referencia": "",
        "uf": "",
        "cep": "",
        "hospital": "Maternidade Frei Justo Venture",
        "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
        "data": date.today(),
        "hora": datetime.now().time().replace(microsecond=0),
        "diagnostico": "",
        "peso": "",
        "antecedente_transfusional": "Não",
        "antecedentes_obstetricos": "Não",
        "modalidade_transfusao": "Rotina",
    }
    
    # Mapeamento de padrões de rótulos
    label_patterns = {
        "nome_paciente": r"Nome\s+do\s+Paciente",
        "nome_genitora": r"Nome\s+da\s+(Mãe|Mae)",
        "nome_responsavel": r"Nome\s+do\s+Responsável",
        "cartao_sus": r"^CNS$",
        "data_nascimento": r"Data\s+de\s+Nasc",
        "sexo": r"^Sexo$",
        "raca": r"Raça[/]?[Cc]or",
        "telefone_contato": r"Telefone\s+de\s+Contato",
        "telefone_celular": r"Telefone\s+Celular",
        "prontuario": r"(Núm\.|Num\.)\s*Prontuário",
        "atendimento": r"^Atendimento$",
        "endereco": r"Endereço\s+Residencial",
        "municipio": r"Munic[íi]pio\s+de\s+Refer[êe]ncia",
        "uf": r"^UF$",
        "cep": r"^CEP$",
        "cpf": r"^CPF$",
    }
    
    # Processar cada linha
    for i, line in enumerate(lines):
        line_text = line["text"]
        
        # Verificar se é linha de rótulos (contém múltiplos campos conhecidos)
        matches = []
        for field_name, pattern in label_patterns.items():
            for match in re.finditer(pattern, line_text, re.IGNORECASE):
                # Encontrar palavras que compõem este rótulo
                label_words = []
                match_start = match.start()
                match_end = match.end()
                
                char_pos = 0
                for word in line["words"]:
                    word_len = len(word["text"])
                    word_start = char_pos
                    word_end = char_pos + word_len
                    
                    # Se palavra está dentro do match
                    if word_end > match_start and word_start < match_end:
                        label_words.append(word)
                    
                    char_pos = word_end + 1  # +1 para espaço
                
                matches.append({
                    "field": field_name,
                    "label": match.group(0),
                    "words": label_words,
                    "match": match
                })
        
        # Se encontrou rótulos, próxima linha tem valores
        if matches and i + 1 < len(lines):
            value_line = lines[i + 1]
            
            for match_info in matches:
                field = match_info["field"]
                label_words = match_info["words"]
                
                # Extrair valor alinhado
                value = match_value_to_label_by_position(
                    label_words,
                    value_line["words"],
                    match_info["label"]
                )
                
                # Processar valor
                if field == "nome_paciente":
                    data["nome_paciente"] = limpar_nome(value)
                
                elif field == "nome_genitora":
                    # Nome da mãe pode vir junto com nome do responsável
                    # Pegar apenas a primeira parte (antes de outro nome próprio longo)
                    parts = value.split()
                    # Pegar até 4 palavras (nome completo típico)
                    if len(parts) > 4:
                        value = " ".join(parts[:4])
                    data["nome_genitora"] = limpar_nome(value)
                
                elif field == "nome_responsavel":
                    # Se nome da mãe ainda vazio, usar responsável
                    if not data["nome_genitora"]:
                        data["nome_genitora"] = limpar_nome(value)
                
                elif field == "cartao_sus":
                    digits = so_digitos(value)
                    if len(digits) >= 15:
                        data["cartao_sus"] = digits[:15]
                    elif len(digits) >= 11:
                        data["cartao_sus"] = digits
                
                elif field == "data_nascimento":
                    data["data_nascimento"] = normaliza_data(value)
                
                elif field == "sexo":
                    if re.search(r"fem", value, re.IGNORECASE):
                        data["sexo"] = "Feminino"
                    elif re.search(r"masc", value, re.IGNORECASE):
                        data["sexo"] = "Masculino"
                
                elif field == "raca":
                    data["raca"] = limpar_nome(value)
                
                elif field == "prontuario":
                    data["prontuario"] = so_digitos(value)
                
                elif field in ["telefone_contato", "telefone_celular"]:
                    if not data["telefone_paciente"]:
                        data["telefone_paciente"] = normaliza_telefone(value)
                
                elif field == "endereco":
                    # Endereço é um campo longo, pegar a linha inteira
                    data["endereco_completo"] = line_text.replace(match_info["label"], "").strip()
                    # Se a linha de valor for mais completa, usar ela
                    if value_line["text"]:
                        data["endereco_completo"] = value_line["text"]
                
                elif field == "municipio":
                    data["municipio_referencia"] = limpar_nome(value)
                
                elif field == "uf":
                    data["uf"] = limpar_nome(value)[:2].upper()
                
                elif field == "cep":
                    data["cep"] = so_digitos(value)[:8]
                
    
    # Tentativa de extrair diagnóstico (se houver)
    for line in lines:
        if re.search(r"diagnóstico", line["text"], re.IGNORECASE):
            # Tenta pegar a linha seguinte como diagnóstico
            try:
                diag_line = lines[lines.index(line) + 1]
                data["diagnostico"] = diag_line["text"]
            except IndexError:
                pass

    return data

# ----------------------------------------------------------
# EXTRAÇÃO COM OCR
# ----------------------------------------------------------
def try_rapid_ocr(img_bytes: bytes):
    """Tenta extrair dados usando OCR"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        
        ocr = RapidOCR()
        
        # O RapidOCR espera um caminho de arquivo ou bytes
        # Criar um buffer para a imagem
        img_buffer = io.BytesIO(img_bytes)
        
        # Executar OCR
        result, _ = ocr(img_buffer.read())
        
        if not result:
            return {}, "OCR não conseguiu ler nenhum texto na imagem."
        
        # Processamento simples do resultado do OCR
        ocr_text = "\n".join([item[1] for item in result])
        
        data = {
            "nome_paciente": "",
            "nome_genitora": "",
            "cartao_sus": "",
            "data_nascimento": "",
            "sexo": "",
            "raca": "",
            "telefone_paciente": "",
            "prontuario": "",
            "endereco_completo": "",
            "municipio_referencia": "",
            "uf": "",
            "cep": "",
            "diagnostico": "",
        }
        
        # Mapeamento de padrões de rótulos para OCR
        ocr_patterns = {
            "nome_paciente": r"Nome\s+do\s+Paciente\s*:\s*(.*)",
            "nome_genitora": r"Nome\s+da\s+(Mãe|Mae)\s*:\s*(.*)",
            "cartao_sus": r"CNS\s*:\s*(\d+)",
            "data_nascimento": r"Data\s+de\s+Nasc\s*:\s*(\d{2}[^\d]?\d{2}[^\d]?\d{4})",
            "prontuario": r"Prontuário\s*:\s*(\d+)",
            "telefone_paciente": r"Telefone\s*:\s*([\d\s\-\(\)]+)",
            "diagnostico": r"Diagnóstico\s*:\s*(.*)",
        }
        
        for field, pattern in ocr_patterns.items():
            match = re.search(pattern, ocr_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if field == "nome_paciente" or field == "nome_genitora":
                    data[field] = limpar_nome(value)
                elif field == "cartao_sus":
                    data[field] = so_digitos(value)
                elif field == "data_nascimento":
                    data[field] = normaliza_data(value)
                elif field == "telefone_paciente":
                    data[field] = normaliza_telefone(value)
                elif field == "prontuario":
                    data[field] = so_digitos(value)
                elif field == "diagnostico":
                    data[field] = value
        
        return data, None
    
    except ImportError:
        return {}, "A biblioteca `rapidocr_onnxruntime` não está instalada. Por favor, adicione-a ao `requirements.txt`."
    except Exception as e:
        st.error(f"Erro ao processar OCR: {e}")
        return {}, f"Erro ao processar OCR: {e}"

# ----------------------------------------------------------
# INICIALIZAÇÃO DE ESTADO
# ----------------------------------------------------------
if "dados" not in st.session_state:
    st.session_state.dados = {
        "nome_paciente": "",
        "nome_genitora": "",
        "cartao_sus": "",
        "data_nascimento": "",
        "sexo": "",
        "raca": "",
        "telefone_paciente": "",
        "prontuario": "",
        "endereco_completo": "",
        "municipio_referencia": "",
        "uf": "",
        "cep": "",
        "hospital": "Maternidade Frei Justo Venture",
        "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
        "data": date.today(),
        "hora": datetime.now().time().replace(microsecond=0),
        "diagnostico": "",
        "peso": "",
        "antecedente_transfusional": "Não",
        "antecedentes_obstetricos": "Não",
        "modalidade_transfusao": "Rotina",
    }
if "origem_dados" not in st.session_state:
    st.session_state.origem_dados = {}

# ----------------------------------------------------------
# INTERFACE
# ----------------------------------------------------------
st.title("Gerador de Ficha HEMOBA AIH")
st.markdown("---")

# Upload
uploaded = st.file_uploader(
    "📤 Carregar AIH (PDF ou Imagem)",
    type=["pdf", "jpg", "jpeg", "png"],
    help="Faça upload do Laudo para Solicitação de Internação Hospitalar"
)

if uploaded:
    file_type = uploaded.type
    
    with st.spinner("🔍 Extraindo dados..."):
        if "pdf" in file_type:
            try:
                pdf_bytes = uploaded.read()
                extracted = parse_aih_tabular(pdf_bytes)
                origem = "AIH"
            except Exception as e:
                st.error(f"Erro ao processar PDF. O Streamlit não travou, mas a extração falhou. Detalhes: {e}")
                # Logar o erro completo para debug
                st.exception(e)
                extracted = {}
                origem = "Erro"
        else:
            img_bytes = uploaded.read()
            extracted, ocr_error = try_rapid_ocr(img_bytes)
            if ocr_error:
                st.error(ocr_error)
                extracted = {}
            origem = "OCR" if not ocr_error else "Erro"
        
        # Atualizar apenas campos não vazios
        for key, value in extracted.items():
            if value and value != "":
                st.session_state.dados[key] = value
                st.session_state.origem_dados[key] = origem
    
    if origem != "Erro":
        st.success(f"✅ Dados extraídos com sucesso via {origem}!")
    
    # CORREÇÃO: Forçar o Streamlit a recriar os widgets do formulário
    # para que eles usem os novos valores de st.session_state.dados
    st.rerun()

# Helper para badges
def badge(field):
    origem = st.session_state.origem_dados.get(field, "Manual")
    if origem == "AIH":
        return '<span class="badge"><span class="dot dot-aih"></span>AIH</span>'
    elif origem == "OCR":
        return '<span class="badge"><span class="dot dot-ocr"></span>OCR</span>'
    else:
        return '<span class="badge"><span class="dot dot-man"></span>Manual</span>'

# Formulário
st.markdown("---")
st.markdown("### 👤 Dados do Paciente")

col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Nome do Paciente** {badge('nome_paciente')}", unsafe_allow_html=True)
    st.session_state.dados["nome_paciente"] = st.text_input(
        "Nome do Paciente",
        value=st.session_state.dados["nome_paciente"],
        label_visibility="collapsed",
        key="input_nome_paciente"
    )

with col2:
    st.markdown(f"**Nome da Mãe/Genitora** {badge('nome_genitora')}", unsafe_allow_html=True)
    st.session_state.dados["nome_genitora"] = st.text_input(
        "Nome da Mãe",
        value=st.session_state.dados["nome_genitora"],
        label_visibility="collapsed",
        key="input_nome_genitora"
    )

col3, col4 = st.columns(2)
with col3:
    st.markdown(f"**CNS/Cartão SUS** {badge('cartao_sus')}", unsafe_allow_html=True)
    st.session_state.dados["cartao_sus"] = st.text_input(
        "CNS",
        value=st.session_state.dados["cartao_sus"],
        label_visibility="collapsed",
        key="input_cartao_sus"
    )

with col4:
    st.markdown(f"**Data de Nascimento** {badge('data_nascimento')}", unsafe_allow_html=True)
    st.session_state.dados["data_nascimento"] = st.text_input(
        "Data de Nascimento",
        value=st.session_state.dados["data_nascimento"],
        label_visibility="collapsed",
        key="input_data_nascimento",
        placeholder="DD/MM/AAAA"
    )

col5, col6 = st.columns(2)
with col5:
    st.markdown(f"**Sexo** {badge('sexo')}", unsafe_allow_html=True)
    sexo_options = ["", "Feminino", "Masculino"]
    sexo_idx = sexo_options.index(st.session_state.dados["sexo"]) if st.session_state.dados["sexo"] in sexo_options else 0
    st.session_state.dados["sexo"] = st.selectbox(
        "Sexo",
        sexo_options,
        index=sexo_idx,
        label_visibility="collapsed",
        key="input_sexo"
    )

with col6:
    st.markdown(f"**Raça/Cor** {badge('raca')}", unsafe_allow_html=True)
    st.session_state.dados["raca"] = st.text_input(
        "Raça/Cor",
        value=st.session_state.dados["raca"],
        label_visibility="collapsed",
        key="input_raca"
    )

col7, col8 = st.columns(2)
with col7:
    st.markdown(f"**Telefone** {badge('telefone_paciente')}", unsafe_allow_html=True)
    st.session_state.dados["telefone_paciente"] = st.text_input(
        "Telefone",
        value=st.session_state.dados["telefone_paciente"],
        label_visibility="collapsed",
        key="input_telefone"
    )

with col8:
    st.markdown(f"**Prontuário** {badge('prontuario')}", unsafe_allow_html=True)
    st.session_state.dados["prontuario"] = st.text_input(
        "Prontuário",
        value=st.session_state.dados["prontuario"],
        label_visibility="collapsed",
        key="input_prontuario"
    )

st.markdown("---")
st.markdown("### 📍 Endereço")

st.markdown(f"**Endereço Completo** {badge('endereco_completo')}", unsafe_allow_html=True)
st.session_state.dados["endereco_completo"] = st.text_area(
    "Endereço",
    value=st.session_state.dados["endereco_completo"],
    label_visibility="collapsed",
    key="input_endereco",
    height=80
)

col9, col10, col11 = st.columns([2, 1, 1])
with col9:
    st.markdown(f"**Município** {badge('municipio_referencia')}", unsafe_allow_html=True)
    st.session_state.dados["municipio_referencia"] = st.text_input(
        "Município",
        value=st.session_state.dados["municipio_referencia"],
        label_visibility="collapsed",
        key="input_municipio"
    )

with col10:
    st.markdown(f"**UF** {badge('uf')}", unsafe_allow_html=True)
    st.session_state.dados["uf"] = st.text_input(
        "UF",
        value=st.session_state.dados["uf"],
        label_visibility="collapsed",
        key="input_uf",
        max_chars=2
    )

with col11:
    st.markdown(f"**CEP** {badge('cep')}", unsafe_allow_html=True)
    st.session_state.dados["cep"] = st.text_input(
        "CEP",
        value=st.session_state.dados["cep"],
        label_visibility="collapsed",
        key="input_cep"
    )

st.markdown("---")
st.markdown("### 🏥 Estabelecimento")

hospital_options = list(HOSPITAIS.keys())
hospital_idx = hospital_options.index(st.session_state.dados["hospital"]) if st.session_state.dados["hospital"] in hospital_options else 0
st.session_state.dados["hospital"] = st.selectbox(
    "Hospital/Unidade",
    hospital_options,
    index=hospital_idx,
    key="input_hospital"
)

telefone_unidade_padrao = HOSPITAIS.get(st.session_state.dados["hospital"], "")
if not st.session_state.dados.get("telefone_unidade"):
    st.session_state.dados["telefone_unidade"] = telefone_unidade_padrao

st.session_state.dados["telefone_unidade"] = st.text_input(
    "Telefone da Unidade",
    value=st.session_state.dados["telefone_unidade"],
    key="input_telefone_unidade"
)

st.markdown("---")
st.markdown("### 📅 Data e Hora")

col12, col13 = st.columns(2)
with col12:
    st.session_state.dados["data"] = st.date_input(
        "Data",
        value=st.session_state.dados["data"],
        key="input_data"
    )

with col13:
    st.session_state.dados["hora"] = st.time_input(
        "Hora",
        value=st.session_state.dados["hora"],
        key="input_hora"
    )

st.markdown("---")
st.markdown("### 🩺 Dados Clínicos")

st.session_state.dados["diagnostico"] = st.text_area(
    "Diagnóstico",
    value=st.session_state.dados["diagnostico"],
    key="input_diagnostico",
    height=80
)

st.session_state.dados["peso"] = st.text_input(
    "Peso (kg)",
    value=st.session_state.dados["peso"],
    key="input_peso"
)

col14, col15 = st.columns(2)
with col14:
    antec_transf_idx = 0 if st.session_state.dados["antecedente_transfusional"] == "Não" else 1
    st.session_state.dados["antecedente_transfusional"] = st.radio(
        "Antecedente Transfusional?",
        ["Não", "Sim"],
        index=antec_transf_idx,
        key="input_antec_transf"
    )

with col15:
    antec_obst_idx = 0 if st.session_state.dados["antecedentes_obstetricos"] == "Não" else 1
    st.session_state.dados["antecedentes_obstetricos"] = st.radio(
        "Antecedentes Obstétricos?",
        ["Não", "Sim"],
        index=antec_obst_idx,
        key="input_antec_obst"
    )

modalidade_options = ["Rotina", "Programada", "Urgência", "Emergência"]
modalidade_idx = modalidade_options.index(st.session_state.dados["modalidade_transfusao"]) if st.session_state.dados["modalidade_transfusao"] in modalidade_options else 0
st.session_state.dados["modalidade_transfusao"] = st.radio(
    "Modalidade de Transfusão",
    modalidade_options,
    index=modalidade_idx,
    key="input_modalidade",
    horizontal=True
)

# ----------------------------------------------------------
# GERAÇÃO DE PDF
# ----------------------------------------------------------
st.markdown("---")
st.markdown("### 📄 Gerar Ficha")

if st.button("🔽 Gerar PDF da Ficha HEMOBA", type="primary", use_container_width=True):
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    
    def gerar_pdf_bytes(d):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        y = h - 40
        
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, y, "FICHA HEMOBA - SOLICITAÇÃO DE TRANSFUSÃO")
        y -= 30
        
        c.setFont("Helvetica", 10)
        
        def line(label, value):
            nonlocal y
            c.drawString(40, y, f"{label}: {value}")
            y -= 18
        
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "DADOS DO PACIENTE")
        y -= 20
        c.setFont("Helvetica", 10)
        
        line("Nome do Paciente", d["nome_paciente"])
        line("Nome da Mãe/Genitora", d["nome_genitora"])
        line("CNS/Cartão SUS", d["cartao_sus"])
        line("Data de Nascimento", d["data_nascimento"])
        line("Sexo", d["sexo"])
        line("Raça/Cor", d["raca"])
        line("Telefone", d["telefone_paciente"])
        line("Prontuário", d["prontuario"])
        
        y -= 10
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "ENDEREÇO")
        y -= 20
        c.setFont("Helvetica", 10)
        
        line("Endereço", d["endereco_completo"])
        line("Município", d["municipio_referencia"])
        line("UF", d["uf"])
        line("CEP", d["cep"])
        
        y -= 10
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "ESTABELECIMENTO")
        y -= 20
        c.setFont("Helvetica", 10)
        
        line("Hospital/Unidade", d["hospital"])
        line("Telefone da Unidade", d["telefone_unidade"])
        
        y -= 10
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "DATA E HORA")
        y -= 20
        c.setFont("Helvetica", 10)
        
        line("Data", str(d["data"]))
        line("Hora", str(d["hora"]))
        
        y -= 10
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "DADOS CLÍNICOS")
        y -= 20
        c.setFont("Helvetica", 10)
        
        line("Diagnóstico", d["diagnostico"])
        line("Peso", d["peso"])
        line("Antecedente Transfusional", d["antecedente_transfusional"])
        line("Antecedentes Obstétricos", d["antecedentes_obstetricos"])
        line("Modalidade de Transfusão", d["modalidade_transfusao"])
        
        c.save()
        buf.seek(0)
        return buf.getvalue()
    
    pdf_data = gerar_pdf_bytes(st.session_state.dados)
    
    st.download_button(
        label="⬇️ Baixar PDF",
        data=pdf_data,
        file_name=f"ficha_hemoba_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
    
    st.success("✅ PDF gerado com sucesso!")

# Debug
with st.expander("🔍 Ver dados extraídos (debug)"):
    st.json(st.session_state.dados)
    st.json(st.session_state.origem_dados)
