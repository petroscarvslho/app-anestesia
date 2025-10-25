import io
import re
import streamlit as st
import fitz
import traceback
from rapidocr_onnxruntime import RapidOCR
from PIL import Image, ImageOps, ExifTags
import numpy as np
import cv2

# --- CONFIGURAÇÃO E FUNÇÕES AUXILIARES ---
st.set_page_config(page_title="Analisador de Laudo AIH", layout="centered")
st.markdown("""<style>.block-container {max-width: 740px !important; padding-top: 1.2rem;}</style>""", unsafe_allow_html=True)

def limpar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", txt).strip() if txt else ""

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

# === ADIÇÃO 1: PAINEL DE QUALIDADE ===
LABEL_PATTERNS = { "LAUDO SOLICITAÇÃO INTERNAÇÃO": r"LAUDO\s+PARA\s+SOLICITA[ÇC][AÃ]O\s+DE\s+INTERNA[ÇC][AÃ]O", "Nome do Paciente": r"Nome\s*do\s*Paciente", "Nome da Mãe": r"Nome\s*da\s*M[ãa]e", "CNS": r"\bCNS\b", "Data de Nasc": r"Data\s*de\s*Nasc", "Endereço Residencial": r"Endere[çc]o\s*Residencial", "Município de Referência": r"Municipio\s*de\s*Refer[êe]ncia", "Diagnóstico Inicial": r"Diagn[oó]stico\s*Inicial", }

def quality_score(text: str):
    if not text: return 0, {}
    found_labels = {label: bool(re.search(pattern, text, re.IGNORECASE)) for label, pattern in LABEL_PATTERNS.items()}
    score = sum(found_labels.values()) / len(LABEL_PATTERNS)
    return score, found_labels

# === ADIÇÃO 2: PAINEL DE VALIDAÇÃO ===
def validate_data(data: dict):
    warnings = []
    # Valida CNS
    cns = data.get("cartao_sus", "")
    if cns and len(so_digitos(cns)) != 15:
        warnings.append(f"⚠️ **CNS inválido:** O valor '{cns}' não parece ter 15 dígitos.")
    # Valida Data de Nascimento
    data_nasc = data.get("data_nascimento", "")
    if data_nasc and not re.match(r"\d{2}/\d{2}/\d{4}", data_nasc):
        warnings.append(f"⚠️ **Data de Nascimento inválida:** O formato de '{data_nasc}' não é DD/MM/AAAA.")
    return warnings

# --- MOTORES DE ANÁLISE (NÃO MUDAM) ---
def parse_pdf_text(full_text: str):
    data = {}
    patterns = { "nome_paciente": r"Nome do Paciente\s+([A-ZÀ-ÿ\s]+?)\s+CNS", "cartao_sus": r"CNS\s+(\d{15})\s+", "nome_genitora": r"Nome da Mãe\s+([A-ZÀ-ÿ\s]+?)\s+Endereço Residencial", "data_nascimento": r"Data de Nasc\s+([\d/]+)\s+Sexo", "sexo": r"Sexo\s+(Feminino|Masculino)\s+Raça/cor", "raca": r"Raça/cor\s+([A-ZÀ-ÿ]+)\s+Nome do Responsável", "telefone_paciente": r"Telefone de Contato\s+([()\d\s-]+?)\s+Telefone Celular", "prontuario": r"Núm\. Prontuário\s+(\d+)\s+Telefone de Contato", "endereco_completo": r"Endereço Residencial \(Rua, Av etc\)\s+(.*?)\s+CPF", "municipio_referencia": r"Municipio de Referência\s+([A-ZÀ-ÿ\s]+?)\s+Cód\. IBGE", "uf": r"UF\s+([A-Z]{2})\s+CEP", "cep": r"CEP\s+([\d.-]+?)\s+Diretor Clinico", "diagnostico": r"Diagnóstico Inicial\s+(.*?)\s+CID 10 Principal", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match: data[field] = limpar_texto(match.group(1))
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    if data.get("cep"): data["cep"] = so_digitos(data["cep"])
    return data

def parse_ocr_text(full_text: str):
    data = {}
    patterns = { "nome_paciente": r"Paciente\s*([A-Z\s]+?)\s*CNS", "cartao_sus": r"CNS\s*(\d{15})", "nome_genitora": r"Mae\s*([A-Z\s]+?)\s*(Feminino|Endereco)", "data_nascimento": r"Nasc\s*([\d/]+)", "sexo": r"(Feminino|Masculino)", "raca": r"Raca/cor\s*([A-Z]+)", "telefone_paciente": r"\((\d{2})\)\s?(\d{4,5}-?\d{4})", "prontuario": r"Prontuario\s*(\d+)", "diagnostico": r"Diagnostico\s*Inicial\s*(.*?)\s*CID", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            value = next((g for g in match.groups() if g is not None and not g.lower() in ['feminino', 'endereco']), None)
            if value: data[field] = limpar_texto(value)
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    return data

# --- PRÉ-PROCESSAMENTO E EXTRAÇÃO (A BASE QUE VOCÊ GOSTOU) ---
@st.cache_resource
def get_ocr_model():
    return RapidOCR()

def preprocess_image_for_ocr(image_bytes: bytes) -> bytes:
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        try: # Autorotação
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation': break
            exif = dict(pil_img._getexif().items())
            if exif[orientation] == 3: pil_img = pil_img.rotate(180, expand=True)
            elif exif[orientation] == 6: pil_img = pil_img.rotate(270, expand=True)
            elif exif[orientation] == 8: pil_img = pil_img.rotate(90, expand=True)
        except (AttributeError, KeyError, IndexError): pass
        
        img_array = np.array(pil_img.convert('RGB'))
        gray_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        processed_img = cv2.adaptiveThreshold(gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15)
        _, buffer = cv2.imencode('.png', processed_img)
        return buffer.tobytes()
    except Exception:
        return image_bytes

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return " ".join(page.get_text(sort=True) for page in doc)

def extract_text_from_image(image_bytes: bytes) -> str:
    processed_bytes = preprocess_image_for_ocr(image_bytes)
    ocr = get_ocr_model()
    result, _ = ocr(processed_bytes)
    if not result: return ""
    full_text = "".join([item[1] for item in result])
    full_text = re.sub(r"([A-Z][a-z]+)", r" \1", full_text)
    full_text = re.sub(r"([A-Z]{2,})", r" \1", full_text)
    return re.sub(r'\s+', ' ', full_text).strip()

# --- LÓGICA PRINCIPAL (COM ADIÇÕES) ---
if "dados" not in st.session_state: st.session_state.dados = {}
if "full_text_debug" not in st.session_state: st.session_state.full_text_debug = ""
if "validation_warnings" not in st.session_state: st.session_state.validation_warnings = []

st.title("Analisador de Laudo AIH")
st.markdown("---")

uploaded = st.file_uploader("📤 Carregar Laudo (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    with st.spinner("🔍 Analisando documento..."):
        try:
            file_bytes = uploaded.read()
            
            if "pdf" in uploaded.type:
                raw_text = extract_text_from_pdf(file_bytes)
                extracted_data = parse_pdf_text(raw_text)
            else:
                raw_text = extract_text_from_image(file_bytes)
                extracted_data = parse_ocr_text(raw_text)
            
            st.session_state.full_text_debug = raw_text
            st.session_state.dados = extracted_data
            
            score, _ = quality_score(raw_text)
            st.session_state.validation_warnings = validate_data(extracted_data)

            if any(extracted_data.values()):
                st.success(f"✅ Documento analisado! Qualidade da Leitura: {score:.0%}")
            else:
                st.warning(f"⚠️ Arquivo lido, mas nenhum dado foi extraído. Qualidade da Leitura: {score:.0%}. Verifique o texto de debug.")
        except Exception as e:
            st.error("Ocorreu um erro crítico ao processar o arquivo.")
            st.session_state.full_text_debug = traceback.format_exc()

# --- INTERFACE DO FORMULÁRIO ---
def get_value(field, default=""):
    return st.session_state.dados.get(field, default)

# ADIÇÃO 2: Exibe o painel de validação se houver alertas
if st.session_state.validation_warnings:
    st.markdown("---")
    with st.container(border=True):
        st.error("Atenção: Alguns dados podem precisar de revisão manual!")
        for warning in st.session_state.validation_warnings:
            st.markdown(warning)

st.markdown("---")
st.markdown("### 👤 Dados do Paciente")
# (O resto do formulário é idêntico...)
col1, col2 = st.columns(2); with col1: st.text_input("Nome do Paciente", get_value("nome_paciente")) 
with col2: st.text_input("Nome da Mãe", get_value("nome_genitora"))
st.markdown("---")
with st.expander("🔍 Ver texto completo extraído e diagnóstico de qualidade"):
    st.code(st.session_state.get("full_text_debug", "Nenhum texto."), language="text")