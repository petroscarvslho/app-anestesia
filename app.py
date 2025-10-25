import io
import re
import streamlit as st
import fitz  # PyMuPDF
import traceback
from rapidocr_onnxruntime import RapidOCR

# ----------------------------------------------------------
# CONFIGURAÇÃO DA PÁGINA E CSS (NÃO MUDA)
# ----------------------------------------------------------
st.set_page_config(page_title="Analisador de Laudo AIH", layout="centered")
MOBILE_CSS = """
<style>
.block-container {max-width: 740px !important; padding-top: 1.2rem;} h1,h2 { letter-spacing: -0.3px; } h3 { margin-top: 1.2rem; }
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# ----------------------------------------------------------
# FUNÇÕES AUXILIARES DE LIMPEZA (NÃO MUDAM)
# ----------------------------------------------------------
def limpar_texto(txt: str) -> str:
    if not txt: return ""
    return re.sub(r"\s+", " ", txt).strip()

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

# ----------------------------------------------------------
# O "MOTOR" DE ANÁLISE DE TEXTO (REGRAS AJUSTADAS)
# ----------------------------------------------------------
def parse_form_text(full_text: str):
    data = {}
    # Regras ajustadas para serem mais robustas com o texto ordenado
    patterns = {
        "nome_paciente": r"Nome do Paciente\s+([A-ZÀ-ÿ\s]+?)\s+CNS",
        "cartao_sus": r"CNS\s+(\d{15})\s+",
        "nome_genitora": r"Nome da Mãe\s+([A-ZÀ-ÿ\s]+?)\s+Endereço Residencial",
        "data_nascimento": r"Data de Nasc\s+([\d/]+)\s+Sexo",
        "sexo": r"Sexo\s+(Feminino|Masculino)\s+Raça/cor",
        "raca": r"Raça/cor\s+([A-ZÀ-ÿ]+)\s+Nome do Responsável",
        "telefone_paciente": r"Telefone de Contato\s+([()\d\s-]+?)\s+Telefone Celular",
        "prontuario": r"Núm\. Prontuário\s+(\d+)\s+Telefone de Contato",
        "endereco_completo": r"Endereço Residencial \(Rua, Av etc\)\s+(.*?)\s+CPF",
        "municipio_referencia": r"Municipio de Referência\s+([A-ZÀ-ÿ\s]+?)\s+Cód\. IBGE",
        "uf": r"UF\s+([A-Z]{2})\s+CEP",
        "cep": r"CEP\s+([\d.-]+?)\s+Diretor Clinico",
        "diagnostico": r"Diagnóstico Inicial\s+(.*?)\s+CID 10 Principal",
    }
    
    for field, pattern in patterns.items():
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            data[field] = limpar_texto(value)
            
    if data.get("cartao_sus"): data["cartao_sus"] = so_digitos(data["cartao_sus"])
    if data.get("cep"): data["cep"] = so_digitos(data["cep"])

    return data

# ----------------------------------------------------------
# FUNÇÕES EXTRATORAS DE TEXTO (MUDANÇA CRÍTICA AQUI!)
# ----------------------------------------------------------
@st.cache_resource
def get_ocr_model():
    return RapidOCR()

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    VERSÃO CORRIGIDA: Usa o método 'sort=True' do PyMuPDF, que organiza o texto
    em uma ordem de leitura natural (de cima para baixo, esquerda para a direita).
    Isso evita o embaralhamento de palavras.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = ""
    for page in doc:
        full_text += page.get_text(sort=True) + " "
    return re.sub(r'\s+', ' ', full_text).strip()

def extract_text_from_image(image_bytes: bytes) -> str:
    ocr = get_ocr_model()
    result, _ = ocr(image_bytes)
    if not result: return ""
    # Junta o resultado do OCR em uma linha única, que é como o extrator de PDF agora funciona
    full_text = " ".join([item[1] for item in result])
    return re.sub(r'\s+', ' ', full_text).strip()

# ----------------------------------------------------------
# LÓGICA PRINCIPAL DO APLICATIVO (SEM st.rerun())
# ----------------------------------------------------------
if "dados" not in st.session_state: st.session_state.dados = {}
if "full_text_debug" not in st.session_state: st.session_state.full_text_debug = "Nenhum arquivo carregado."

st.title("Analisador de Laudo AIH")
st.markdown("---")

uploaded = st.file_uploader("📤 Carregar Laudo (PDF ou Imagem)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded:
    with st.spinner("🔍 Analisando documento..."):
        try:
            file_bytes = uploaded.read()
            
            if "pdf" in uploaded.type:
                raw_text = extract_text_from_pdf(file_bytes)
            else:
                raw_text = extract_text_from_image(file_bytes)
            
            st.session_state.full_text_debug = raw_text
            extracted_data = parse_form_text(raw_text)
            st.session_state.dados = extracted_data

            if any(extracted_data.values()):
                st.success("✅ Documento analisado com sucesso!")
            else:
                st.warning("⚠️ Arquivo lido, mas nenhum dado foi extraído. Verifique o texto de debug.")
        
        except Exception as e:
            st.error("Ocorreu um erro crítico ao processar o arquivo.")
            st.session_state.full_text_debug = traceback.format_exc()

# ----------------------------------------------------------
# INTERFACE DO FORMULÁRIO (RENDERIZAÇÃO)
# ----------------------------------------------------------
def get_value(field, default=""):
    # Esta função agora preenche os campos do formulário com os dados extraídos
    return st.session_state.dados.get(field, default)

st.markdown("---")
st.markdown("### 👤 Dados do Paciente")

col1, col2 = st.columns(2)
with col1:
    st.text_input("Nome do Paciente", get_value("nome_paciente"), key="nome_paciente")
with col2:
    st.text_input("Nome da Mãe", get_value("nome_genitora"), key="nome_genitora")
# ... O resto do formulário continua o mesmo ...
col3, col4 = st.columns(2)
with col3:
    st.text_input("CNS (Cartão SUS)", get_value("cartao_sus"), key="cartao_sus")
with col4:
    st.text_input("Data de Nascimento", get_value("data_nascimento"), key="data_nascimento")
col5, col6 = st.columns(2)
with col5:
    sexo_options = ["", "Feminino", "Masculino"]
    sexo_val = get_value("sexo", "")
    sexo_idx = sexo_options.index(sexo_val) if sexo_val in sexo_options else 0
    st.selectbox("Sexo", sexo_options, index=sexo_idx, key="sexo")
with col6:
    st.text_input("Raça/Cor", get_value("raca"), key="raca")
col7, col8 = st.columns(2)
with col7:
    st.text_input("Telefone de Contato", get_value("telefone_paciente"), key="telefone_paciente")
with col8:
    st.text_input("Nº Prontuário", get_value("prontuario"), key="prontuario")
st.markdown("---")
st.markdown("### 📍 Endereço")
st.text_area("Endereço Completo", get_value("endereco_completo"), key="endereco_completo", height=80)
col9, col10, col11 = st.columns([2, 1, 1])
with col9:
    st.text_input("Município", get_value("municipio_referencia"), key="municipio_referencia")
with col10:
    st.text_input("UF", get_value("uf"), key="uf", max_chars=2)
with col11:
    st.text_input("CEP", get_value("cep"), key="cep")
st.markdown("---")
st.markdown("### 🩺 Dados Clínicos")
st.text_area("Diagnóstico Inicial", get_value("diagnostico"), key="diagnostico", height=100)
with st.expander("🔍 Ver texto completo extraído (debug)"):
    st.code(st.session_state.full_text_debug, language="text")
