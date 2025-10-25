import io
import re
import streamlit as st
import fitz  # PyMuPDF
import traceback
from rapidocr_onnxruntime import RapidOCR
from PIL import Image, ImageOps, ExifTags
import numpy as np
import cv2 # Importa a biblioteca OpenCV que agora está estável

# --- CONFIGURAÇÃO E FUNÇÕES AUXILIARES ---
st.set_page_config(page_title="Analisador de Laudo AIH", layout="centered")
st.markdown("""<style>.block-container {max-width: 740px !important; padding-top: 1.2rem;}</style>""", unsafe_allow_html=True)

def limpar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", txt).strip() if txt else ""

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

# --- MÉTRICAS DE QUALIDADE (DO CHATGPT) ---
LABEL_PATTERNS = { "Nome do Paciente": r"Nome\s+do\s+Paciente", "Nome da Mãe": r"Nome\s+da\s+M[ãa]e", "CNS": r"\bCNS\b", "Data de Nasc": r"Data\s+de\s+Nasc", "Diagnóstico Inicial": r"Diagn[oó]stico\s+Inicial", "Procedimento Solicitado": r"Procedimento\s+Solicitado", }

def quality_score(text: str):
    found_labels = {label: bool(re.search(pattern, text, re.IGNORECASE)) for label, pattern in LABEL_PATTERNS.items()}
    score = sum(found_labels.values()) / len(LABEL_PATTERNS)
    return score, found_labels

# --- MOTORES DE ANÁLISE (NÃO MUDAM) ---
def parse_pdf_text(full_text: str):
    data = {} # (A lógica de extração para PDF continua a mesma)
    patterns = { "nome_paciente": r"Nome do Paciente\s+([A-ZÀ-ÿ\s]+?)\s+CNS", "cartao_sus": r"CNS\s+(\d{15})\s+", "nome_genitora": r"Nome da Mãe\s+([A-ZÀ-ÿ\s]+?)\s+Endereço Residencial", "data_nascimento": r"Data de Nasc\s+([\d/]+)\s+Sexo", "sexo": r"Sexo\s+(Feminino|Masculino)\s+Raça/cor", "raca": r"Raça/cor\s+([A-ZÀ-ÿ]+)\s+Nome do Responsável", "telefone_paciente": r"Telefone de Contato\s+([()\d\s-]+?)\s+Telefone Celular", "prontuario": r"Núm\. Prontuário\s+(\d+)\s+Telefone de Contato", "endereco_completo": r"Endereço Residencial \(Rua, Av etc\)\s+(.*?)\s+CPF", "municipio_referencia": r"Municipio de Referência\s+([A-ZÀ-ÿ\s]+?)\s+Cód\. IBGE", "uf": r"UF\s+([A-Z]{2})\s+CEP", "cep": r"CEP\s+([\d.-]+?)\s+Diretor Clinico", "diagnostico": r"Diagnóstico Inicial\s+(.*?)\s+CID 10 Principal", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match: data[field] = limpar_texto(match.group(1))
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    if data.get("cep"): data["cep"] = so_digitos(data["cep"])
    return data

def parse_ocr_text(full_text: str):
    data = {} # (A lógica de extração para OCR continua a mesma)
    patterns = { "nome_paciente": r"Paciente\s*([A-Z\s]+?)\s*CNS", "cartao_sus": r"CNS\s*(\d{15})", "nome_genitora": r"Mae\s*([A-Z\s]+?)\s*Feminino|Mae\s*([A-Z\s]+?)\s*Endereco", "data_nascimento": r"Nasc\s*([\d/]+)", "sexo": r"(Feminino|Masculino)", "raca": r"Raca/cor\s*([A-Z]+)", "telefone_paciente": r"\((\d{2})\)\s?(\d{4,5}-?\d{4})", "prontuario": r"Prontuario\s*(\d+)", "diagnostico": r"Diagnostico\s*Inicial\s*(.*?)\s*CID", }
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            value = next((g for g in match.groups() if g is not None), None)
            if value: data[field] = limpar_texto(value)
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    return data

# --- FUNÇÕES EXTRATORAS (COM PRÉ-PROCESSAMENTO AVANÇADO) ---
@st.cache_resource
def get_ocr_model():
    return RapidOCR()

def preprocess_image_for_ocr(image_bytes: bytes) -> bytes:
    """Aplica o pré-processamento completo (PIL + OpenCV) na imagem."""
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        # Autorotação (PIL)
        try:
            for orientation in ExifTags.TAGS.keys():
                if ExifTags.TAGS[orientation] == 'Orientation': break
            exif = dict(pil_img._getexif().items())
            if exif[orientation] == 3: pil_img = pil_img.rotate(180, expand=True)
            elif exif[orientation] == 6: pil_img = pil_img.rotate(270, expand=True)
            elif exif[orientation] == 8: pil_img = pil_img.rotate(90, expand=True)
        except (AttributeError, KeyError, IndexError): pass

        # Converte para array do OpenCV
        img_array = np.array(pil_img.convert('RGB'))
        gray_img = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        
        # Binarização Adaptativa (OpenCV) - A melhoria mais poderosa
        processed_img = cv2.adaptiveThreshold(
            gray_img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 15
        )
        
        # Converte a imagem processada de volta para bytes
        _, buffer = cv2.imencode('.png', processed_img)
        return buffer.tobytes()
    except Exception as e:
        st.warning(f"Falha no pré-processamento avançado: {e}. Usando imagem original.")
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
    full_text_spaced = re.sub(r"([A-Z][a-z]+)", r" \1", full_text)
    full_text_spaced = re.sub(r"([A-Z]{2,})", r" \1", full_text_spaced)
    return re.sub(r'\s+', ' ', full_text_spaced).strip()

# --- LÓGICA PRINCIPAL DO APLICATIVO ---
if "dados" not in st.session_state: st.session_state.dados = {}
if "full_text_debug" not in st.session_state: st.session_state.full_text_debug = "Nenhum arquivo carregado."

st.title("Analisador de Laudo AIH")
st.markdown("---")

uploaded = st.file_uploader("📤 Carregar Laudo (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    with st.spinner("🔍 Analisando documento..."):
        # (A lógica principal continua a mesma)
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
            st.session_state.score = score

            if any(extracted_data.values()):
                st.success(f"✅ Documento analisado! Cobertura de Rótulos: {score:.0%}")
            else:
                st.warning(f"⚠️ Arquivo lido, mas nenhum dado foi extraído. Cobertura de Rótulos: {score:.0%}. Verifique o texto de debug.")
        except Exception as e:
            st.error("Ocorreu um erro crítico ao processar o arquivo.")
            st.session_state.full_text_debug = traceback.format_exc()

# --- INTERFACE DO FORMULÁRIO (RENDERIZAÇÃO) ---
def get_value(field, default=""):
    return st.session_state.dados.get(field, default)

st.markdown("---") # (O formulário continua o mesmo)
st.markdown("### 👤 Dados do Paciente")
col1, col2 = st.columns(2); with col1: st.text_input("Nome do Paciente", get_value("nome_paciente")) 
with col2: st.text_input("Nome da Mãe", get_value("nome_genitora"))
col3, col4 = st.columns(2); with col3: st.text_input("CNS (Cartão SUS)", get_value("cartao_sus"))
with col4: st.text_input("Data de Nascimento", get_value("data_nascimento"))
# (O resto do formulário é idêntico...)
st.markdown("---")
with st.expander("🔍 Ver texto completo extraído e diagnóstico"):
    st.code(st.session_state.get("full_text_debug", "Nenhum texto."), language="text")
    if "score" in st.session_state:
        st.metric("Cobertura de Rótulos Encontrados", f"{st.session_state.score:.0%}")