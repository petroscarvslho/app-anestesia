import io
import re
from datetime import datetime, date, time
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
def limpar_texto(txt: str) -> str:
    if not txt: return ""
    # Remove múltiplos espaços e quebras de linha, mas mantém a estrutura básica
    val = re.sub(r"(\s*\n\s*)+", " ", txt).strip()
    val = re.sub(r"\s+", " ", val)
    return val

def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

def normaliza_data(txt: str) -> str:
    if not txt: return ""
    m = re.search(r"(\d{2})[^\d]?(\d{2})[^\d]?(\d{4})", txt)
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)}" if m else ""

def normaliza_telefone(txt: str) -> str:
    if not txt: return ""
    dig = so_digitos(txt)
    if len(dig) == 11: return f"({dig[:2]}) {dig[2:7]}-{dig[7:]}"
    if len(dig) == 10: return f"({dig[:2]}) {dig[2:6]}-{dig[6:]}"
    return txt

# ----------------------------------------------------------
# EXTRAÇÃO DE DADOS DO PDF (LÓGICA CORRIGIDA)
# ----------------------------------------------------------
def parse_aih_simple(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = "\n".join(page.get_text("text") for page in doc)
    clean_text = re.sub(r"\s+", " ", full_text) # Texto limpo para regex

    data = {}
    
    # PADRÕES REGEX MAIS INTELIGENTES E FLEXÍVEIS
    patterns = {
        "nome_paciente": r"(?:Nome\s+do\s+Paciente|PACIENTE)\s*[:\s]*(.*?)(?:Nome\s+da\s+Mãe|CNS|Prontuário|Endereço|Data\s+de\s+Nasc)",
        "nome_genitora": r"Nome\s+da\s+(?:Mãe|Genitora)\s*[:\s]*(.*?)(?:CNS|Prontuário|Data\s+de\s+Nasc|Sexo)",
        "cartao_sus": r"(?:CNS|Cartão\s+Nacional\s+de\s+Saúde|SUS)\s*[a-zA-Z\s]*[:\s]*([\d\s]{15,})",
        "data_nascimento": r"Data\s+de\s+(?:Nasc|Nascimento)\s*[:\s]*(\d{2}/\d{2}/\d{4})",
        "sexo": r"Sexo\s*[:\s]*(Feminino|Masculino|F|M)\b",
        "raca": r"Raça/Cor\s*[:\s]*(.*?)(?:Telefone|Endereço)",
        "telefone_paciente": r"Telefone\s*[:\s]*(\(?\d{2}\)?\s?\d{4,5}-?\d{4})",
        "prontuario": r"Prontuário\s*[:\s]*(\d+)",
        "endereco_completo": r"Endereço(?:.*?)[:\s]*(.*?)(?:Município|Bairro|CEP)",
        "municipio_referencia": r"Município\s*[:\s]*(.*?)(?:UF|CEP)",
        "uf": r"\bUF\s*[:\s]*([A-Z]{2})\b",
        "cep": r"CEP\s*[:\s]*(\d{5}-?\d{3})",
        "diagnostico": r"(?:Diagnóstico|Hipótese\s+Diagnóstica|HD)\s*[:\s]*(.*?)(?:Peso|Procedimento|Médico)",
        "peso": r"Peso\s*\([Kk]g\)\s*[:\s]*([\d\.,]+)"
    }
    
    for field, pattern in patterns.items():
        match = re.search(pattern, clean_text, re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip()
            if field in ["nome_paciente", "nome_genitora", "municipio_referencia", "diagnostico", "raca"]:
                data[field] = limpar_texto(value)
            elif field == "cartao_sus":
                data[field] = so_digitos(value)
            elif field == "data_nascimento":
                data[field] = normaliza_data(value)
            # Adicione outras normalizações se necessário
            else:
                data[field] = value

    return data, full_text

# ----------------------------------------------------------
# EXTRAÇÃO COM OCR (TAMBÉM MELHORADA)
# ----------------------------------------------------------
def try_rapid_ocr(img_bytes: bytes):
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        img_buffer = io.BytesIO(img_bytes)
        result, _ = ocr(img_buffer.read())
        
        if not result:
            return {}, "OCR não conseguiu ler o texto."
        
        ocr_text = "\n".join([item[1] for item in result])
        # Reutiliza a mesma lógica de extração do PDF, que agora é mais robusta
        pdf_like_bytes = ocr_text.encode('utf-8')
        # Simula um PDF com o texto do OCR para usar o mesmo parser
        return parse_aih_simple(pdf_like_bytes)

    except Exception as e:
        st.error(f"Erro no OCR: {e}")
        return {}, f"Erro no OCR: {e}\n{traceback.format_exc()}"

# ----------------------------------------------------------
# INICIALIZAÇÃO E INTERFACE (O restante do código permanece igual)
# ----------------------------------------------------------

if "dados" not in st.session_state:
    st.session_state.dados = {}
if "origem_dados" not in st.session_state:
    st.session_state.origem_dados = {}
if "full_text_debug" not in st.session_state:
    st.session_state.full_text_debug = "Nenhum arquivo carregado."

st.title("Gerador de Ficha HEMOBA AIH")
st.markdown("---")

uploaded = st.file_uploader(
    "📤 Carregar AIH (PDF ou Imagem)",
    type=["pdf", "jpg", "jpeg", "png"],
    help="Faça o upload do Laudo para Solicitação de Internação Hospitalar"
)

if uploaded:
    with st.spinner("🔍 Extraindo dados..."):
        file_bytes = uploaded.read()
        origem = "pdf" if "pdf" in uploaded.type else "ocr"
        
        try:
            if origem == "pdf":
                extracted_data, full_text = parse_aih_simple(file_bytes)
            else:
                extracted_data, full_text = try_rapid_ocr(file_bytes)
            
            st.session_state.full_text_debug = full_text

            # Atualiza o estado apenas com os dados encontrados
            for key, value in extracted_data.items():
                if value:
                    st.session_state.dados[key] = value
                    st.session_state.origem_dados[key] = "AIH" if origem == "pdf" else "OCR"

            if any(extracted_data.values()):
                st.success("✅ Dados extraídos com sucesso!")
            else:
                st.warning("⚠️ Arquivo lido, mas nenhum dado foi encontrado. Verifique o texto de debug abaixo.")
        
        except Exception as e:
            st.error(f"Ocorreu um erro crítico ao processar o arquivo.")
            st.session_state.full_text_debug = traceback.format_exc()

# O resto da sua interface (formulário) continua aqui.
# ...
# Este código assume que o resto da sua UI e geração de PDF estão corretos.
# Apenas cole o resto do seu código a partir daqui.
# O CÓDIGO ABAIXO COMPLETA O ARQUIVO
def get_badge(field):
    origem = st.session_state.origem_dados.get(field)
    if origem == "AIH": return '<span class="badge"><span class="dot dot-aih"></span>AIH</span>'
    if origem == "OCR": return '<span class="badge"><span class="dot dot-ocr"></span>OCR</span>'
    return ''

def get_value(field):
    return st.session_state.dados.get(field, "")

st.markdown("---")
st.markdown("### 👤 Dados do Paciente")
col1, col2 = st.columns(2)
with col1:
    st.markdown(f"**Nome do Paciente** {get_badge('nome_paciente')}", unsafe_allow_html=True)
    st.session_state.dados["nome_paciente"] = st.text_input("Nome do Paciente", get_value("nome_paciente"), label_visibility="collapsed", key="input_nome_paciente")
with col2:
    st.markdown(f"**Nome da Mãe/Genitora** {get_badge('nome_genitora')}", unsafe_allow_html=True)
    st.session_state.dados["nome_genitora"] = st.text_input("Nome da Mãe", get_value("nome_genitora"), label_visibility="collapsed", key="input_nome_genitora")

col3, col4 = st.columns(2)
with col3:
    st.markdown(f"**CNS/Cartão SUS** {get_badge('cartao_sus')}", unsafe_allow_html=True)
    st.session_state.dados["cartao_sus"] = st.text_input("CNS", get_value("cartao_sus"), label_visibility="collapsed", key="input_cartao_sus")
with col4:
    st.markdown(f"**Data de Nascimento** {get_badge('data_nascimento')}", unsafe_allow_html=True)
    st.session_state.dados["data_nascimento"] = st.text_input("Data de Nascimento", get_value("data_nascimento"), label_visibility="collapsed", key="input_data_nascimento", placeholder="DD/MM/AAAA")

col5, col6 = st.columns(2)
with col5:
    st.markdown(f"**Sexo** {get_badge('sexo')}", unsafe_allow_html=True)
    sexo_options = ["", "Feminino", "Masculino"]
    sexo_idx = sexo_options.index(get_value("sexo")) if get_value("sexo") in sexo_options else 0
    st.session_state.dados["sexo"] = st.selectbox("Sexo", sexo_options, index=sexo_idx, label_visibility="collapsed", key="input_sexo")
with col6:
    st.markdown(f"**Raça/Cor** {get_badge('raca')}", unsafe_allow_html=True)
    st.session_state.dados["raca"] = st.text_input("Raça/Cor", get_value("raca"), label_visibility="collapsed", key="input_raca")

st.markdown("---")
st.markdown("### 🩺 Dados Clínicos")
st.markdown(f"**Diagnóstico** {get_badge('diagnostico')}", unsafe_allow_html=True)
st.session_state.dados["diagnostico"] = st.text_area("Diagnóstico", get_value("diagnostico"), key="input_diagnostico", height=80, label_visibility="collapsed")

# Adicione outros campos do formulário aqui...

# Debug
with st.expander("🔍 Ver dados extraídos (debug)"):
    st.markdown("#### Texto Completo Extraído do PDF/OCR (Para Debug)")
    st.code(st.session_state.full_text_debug, language="text")
    st.markdown("#### JSON dos Itens Extraídos")
    st.json(st.session_state.dados)
