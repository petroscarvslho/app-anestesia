import io
import re
import streamlit as st
import fitz  # PyMuPDF
import traceback
from rapidocr_onnxruntime import RapidOCR

# --- CONFIGURAÇÃO E FUNÇÕES AUXILIARES ---
st.set_page_config(page_title="Diagnóstico de Extração", layout="wide")
st.title("🔬 Diagnóstico de Extração de Texto (Versão Segura)")
st.info("Foco: Melhorar a extração de texto de PDFs e medir a qualidade da extração em ambos os formatos.")

def limpar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", txt).strip() if txt else ""

# --- MÉTRICAS DE QUALIDADE (IDEIA DO CHATGPT) ---
# Rótulos-chave que esperamos encontrar em um formulário AIH
LABEL_PATTERNS = {
    "LAUDO SOLICITAÇÃO INTERNAÇÃO": r"LAUDO\s+PARA\s+SOLICITA[ÇC][AÃ]O\s+DE\s+INTERNA[ÇC][AÃ]O",
    "Nome do Paciente": r"Nome\s+do\s+Paciente",
    "Nome da Mãe": r"Nome\s+da\s+M[ãa]e",
    "CNS": r"\bCNS\b",
    "Data de Nasc": r"Data\s+de\s+Nasc",
    "Endereço Residencial": r"Endere[çc]o\s+Residencial",
    "Município de Referência": r"Munic[ií]pio\s+de\s+Refer[êe]ncia",
    "Diagnóstico Inicial": r"Diagn[oó]stico\s+Inicial",
    "Procedimento Solicitado": r"Procedimento\s+Solicitado",
}

def quality_score(text: str):
    """Calcula a porcentagem de rótulos-chave encontrados no texto."""
    found_labels = {label: bool(re.search(pattern, text, re.IGNORECASE)) for label, pattern in LABEL_PATTERNS.items()}
    score = sum(found_labels.values()) / len(LABEL_PATTERNS)
    return score, found_labels

# --- FUNÇÕES EXTRATORAS DE TEXTO (PDF MELHORADO) ---
@st.cache_resource
def get_ocr_model():
    return RapidOCR()

def extract_text_from_pdf_improved(pdf_bytes: bytes) -> str:
    """
    VERSÃO MELHORADA: Usa 'blocks' para manter a estrutura de linhas, 
    aumentando a qualidade do texto para análise.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        blocks = sorted(page.get_text("blocks"), key=lambda b: b[1])
        for block in blocks:
            full_text += block[4].replace('\n', ' ').strip() + "\n"
    # Limpa linhas vazias em excesso, mas mantém a estrutura
    return re.sub(r'\n{2,}', '\n', full_text).strip()

def extract_text_from_image_simple(image_bytes: bytes) -> str:
    """Mantém a extração de imagem simples e estável, sem OpenCV."""
    ocr = get_ocr_model()
    result, _ = ocr(image_bytes)
    if not result: return "OCR não encontrou nenhum texto."
    # Junta o texto do OCR com quebras de linha para simular um layout
    full_text = "\n".join([item[1] for item in result])
    return full_text.strip()

# --- INTERFACE DA FERRAMENTA DE DIAGNÓSTICO APRIMORADA ---
st.markdown("---")
st.header("1. Teste de PDF (Extração Melhorada)")
pdf_upload = st.file_uploader("Carregue um PDF", type="pdf", key="pdf_direct")

if pdf_upload:
    with st.spinner("Extraindo texto do PDF..."):
        pdf_bytes = pdf_upload.read()
        extracted_text = extract_text_from_pdf_improved(pdf_bytes)
        score, found_labels = quality_score(extracted_text)
        
        col1, col2 = st.columns([2, 1])
        with col1:
            st.text_area("📄 Texto Extraído (PDF)", extracted_text, height=300)
        with col2:
            st.metric("Cobertura de Rótulos", f"{score:.0%}")
            st.write("**Rótulos Encontrados:**")
            st.json({label: status for label, status in found_labels.items() if status})
            st.write("**Rótulos Ausentes:**")
            st.json({label: status for label, status in found_labels.items() if not status})

st.markdown("---")
st.header("2. Teste de Imagem (Extração Simples)")
image_upload = st.file_uploader("Carregue uma Imagem", type=["png", "jpg", "jpeg"], key="image_ocr")

if image_upload:
    with st.spinner("Analisando imagem com OCR..."):
        image_bytes = image_upload.read()
        extracted_text = extract_text_from_image_simple(image_bytes)
        score, found_labels = quality_score(extracted_text)
        
        col1, col2 = st.columns([2, 1])
        with col1:
            st.text_area("🖼️ Texto Extraído (Imagem)", extracted_text, height=300)
        with col2:
            st.metric("Cobertura de Rótulos", f"{score:.0%}")
            st.write("**Rótulos Encontrados:**")
            st.json({label: status for label, status in found_labels.items() if status})
            st.write("**Rótulos Ausentes:**")
            st.json({label: status for label, status in found_labels.items() if not status})