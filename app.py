import io
import re
from datetime import datetime, date, time
from collections import defaultdict

import streamlit as st
import fitz  # PyMuPDF

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
    # Esta regex precisa ser ajustada para extrair o nome completo de um match.group(0)
    # Se o txt for um objeto match, precisamos do grupo. Se for uma string, processamos.
    if hasattr(txt, 'group'):
        txt = txt.group(1) if txt.group(1) else txt.group(0)
    
    partes = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]+", txt)
    val = " ".join(partes).strip()
    val = re.sub(r"\s+", " ", val)
    return val

def so_digitos(txt: str) -> str:
    if hasattr(txt, 'group'):
        txt = txt.group(1) if txt.group(1) else txt.group(0)
    return re.sub(r"\D", "", txt or "")

def normaliza_data(txt: str) -> str:
    if hasattr(txt, 'group'):
        txt = txt.group(1) if txt.group(1) else txt.group(0)
    if not txt:
        return ""
    m = re.search(r"(\d{2})[^\d]?(\d{2})[^\d]?(\d{4})", txt)
    if not m:
        return ""
    d, mth, y = m.groups()
    return f"{d}/{mth}/{y}"

def normaliza_telefone(txt: str) -> str:
    if hasattr(txt, 'group'):
        txt = txt.group(1) if txt.group(1) else txt.group(0)
    if not txt:
        return ""
    dig = so_digitos(txt)
    if len(dig) == 11:
        return f"({dig[:2]}) {dig[2:7]}-{dig[7:]}"
    elif len(dig) == 10:
        return f"({dig[:2]}) {dig[2:6]}-{dig[6:]}"
    return txt

# ----------------------------------------------------------
# EXTRAÇÃO COM PARSER SIMPLES (MAIS ROBUSTO CONTRA TIMEOUT)
# ----------------------------------------------------------
def extract_pdf_text_simple(pdf_bytes: bytes) -> str:
    """Extrai o texto completo do PDF de forma simples."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        # Usar get_text("text") é a forma mais simples e menos propensa a travar
        text += page.get_text("text") + "\n"
    return text

def parse_aih_simple(pdf_bytes: bytes):
    """Parser simples baseado em texto completo e regex."""
    full_text = extract_pdf_text_simple(pdf_bytes)
    
    # Pré-processamento: remover quebras de linha e múltiplos espaços
    # Normalizar o texto para facilitar a regex.
    full_text_normalized = re.sub(r'[\r\n]+', ' ', full_text).strip()
    full_text_normalized = re.sub(r'\s+', ' ', full_text_normalized)
    
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
    
    # Helper para encontrar valor após um rótulo
    # Procura o rótulo e captura o que vier logo após (até o próximo rótulo ou fim da string)
    def find_value(pattern, text, cleanup_func=None):
        # A regex tenta capturar o texto após o rótulo, parando antes de um novo rótulo
        # (assumindo que rótulos são em MAIÚSCULAS ou começam com uma palavra chave)
        match = re.search(pattern + r"[:\s]+(.+?)(?=\s*[A-Z]{2,}:|\s*[A-Z][a-z]+:|\Z)", text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if cleanup_func:
                return cleanup_func(value)
            return value
        return ""

    # Extração de Dados
    
    # Nome do Paciente: Procura por "Nome do Paciente" e captura o que vier depois
    nome_paciente_match = re.search(r"Nome\s+do\s+Paciente[:\s]+(.+?)(?=\s*Nome\s+da\s+(Mãe|Mae)|\s*CNS:|\Z)", full_text_normalized, re.IGNORECASE)
    if nome_paciente_match:
        data["nome_paciente"] = limpar_nome(nome_paciente_match.group(1))

    # CNS/Cartão SUS: Procura por "CNS" e captura 15 dígitos
    cartao_sus_match = re.search(r"CNS[:\s]+(\d{15})", full_text_normalized, re.IGNORECASE)
    if cartao_sus_match:
        data["cartao_sus"] = so_digitos(cartao_sus_match.group(1))
    
    # Data de Nascimento: Procura por "Data de Nasc" e captura a data
    data_nasc_match = re.search(r"Data\s+de\s+Nasc[:\s]+(\d{2}[^\d]?\d{2}[^\d]?\d{4})", full_text_normalized, re.IGNORECASE)
    if data_nasc_match:
        data["data_nascimento"] = normaliza_data(data_nasc_match.group(1))
    
    # Sexo: Procura por "Sexo" e captura "Feminino", "Masculino", "F" ou "M"
    sexo_match = re.search(r"Sexo[:\s]+(Feminino|Masculino|F|M)", full_text_normalized, re.IGNORECASE)
    if sexo_match:
        sexo = sexo_match.group(1).upper()
        data["sexo"] = "Feminino" if sexo.startswith("F") else "Masculino" if sexo.startswith("M") else ""
        
    # Nome da Mãe/Genitora: Procura por "Nome da Mãe" ou "Nome do Responsável"
    nome_mae_match = re.search(r"Nome\s+da\s+(Mãe|Mae|Responsável)[:\s]+(.+?)(?=\s*CNS:|\s*Data\s+de\s+Nasc:|\Z)", full_text_normalized, re.IGNORECASE)
    if nome_mae_match:
        data["nome_genitora"] = limpar_nome(nome_mae_match.group(2))

    # Telefone: Procura por "Telefone" e captura o número
    telefone_match = re.search(r"Telefone\s+(de\s+Contato|Celular)?:?\s*(\(?\d{2}\)?\s*\d{4,5}-?\d{4})", full_text_normalized, re.IGNORECASE)
    if telefone_match:
        data["telefone_paciente"] = normaliza_telefone(telefone_match.group(2))
    
    # Prontuário: Procura por "Prontuário" e captura os dígitos
    prontuario_match = re.search(r"Prontuário[:\s]+(\d+)", full_text_normalized, re.IGNORECASE)
    if prontuario_match:
        data["prontuario"] = so_digitos(prontuario_match.group(1))

    # Endereço: Procura por "Endereço Residencial" e captura o que vier depois
    endereco_match = re.search(r"Endereço\s+Residencial[:\s]+(.+?)(?=\s*Munic[íi]pio:|\Z)", full_text_normalized, re.IGNORECASE)
    if endereco_match:
        data["endereco_completo"] = limpar_nome(endereco_match.group(1))
        
    # Município: Procura por "Município de Referência" e captura o nome
    municipio_match = re.search(r"Munic[íi]pio\s+de\s+Refer[êe]ncia[:\s]+(.+?)(?=\s*UF:|\Z)", full_text_normalized, re.IGNORECASE)
    if municipio_match:
        data["municipio_referencia"] = limpar_nome(municipio_match.group(1))
        
    # UF: Procura por "UF" e captura 2 letras
    uf_match = re.search(r"UF[:\s]+([A-Z]{2})", full_text_normalized, re.IGNORECASE)
    if uf_match:
        data["uf"] = uf_match.group(1).upper()
        
    # CEP: Procura por "CEP" e captura o formato
    cep_match = re.search(r"CEP[:\s]+(\d{5}-?\d{3})", full_text_normalized, re.IGNORECASE)
    if cep_match:
        digits = so_digitos(cep_match.group(1))
        if len(digits) == 8:
            data["cep"] = f"{digits[:5]}-{digits[5:]}"
            
    # Diagnóstico: Procura por "Diagnóstico" e captura o texto (mais difícil, pode pegar muito)
    diagnostico_match = re.search(r"Diagnóstico[:\s]+(.+?)(?=\s*Peso:|\Z)", full_text_normalized, re.IGNORECASE)
    if diagnostico_match:
        data["diagnostico"] = diagnostico_match.group(1).strip()
        
    # Peso: Procura por "Peso" e captura o valor
    peso_match = re.search(r"Peso[:\s]+(\d+([.,]\d+)?)\s*(kg|Kg)", full_text_normalized, re.IGNORECASE)
    if peso_match:
        data["peso"] = peso_match.group(1)
    
    return data

def parse_aih_tabular(pdf_bytes: bytes): # Manter o nome para compatibilidade
    return parse_aih_simple(pdf_bytes)

# ----------------------------------------------------------
# EXTRAÇÃO COM OCR (para imagens)
# ----------------------------------------------------------
def try_rapid_ocr(img_bytes: bytes):
    """Tenta usar OCR para extrair dados de uma imagem."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr_engine = RapidOCR()
        
        # Simulação de OCR para extrair texto e dados
        # Na realidade, o OCR precisaria de uma imagem, não bytes, e faria a detecção de texto.
        # Aqui, vamos simular que ele extrai o texto completo da imagem.
        
        # Se você precisar de OCR, o ideal é usar um serviço externo ou uma biblioteca
        # que funcione bem no Streamlit Cloud.
        
        return {}, "A funcionalidade de OCR não está implementada de forma robusta e pode falhar. Por favor, use um PDF."
        
        # Se estivesse implementado:
        # result, _ = ocr_engine(img_bytes)
        # full_text = "\n".join([line[1] for line in result])
        
        # data = {}
        # # Lógica de extração baseada em texto completo (regex)
        # # ...
        
        # return data, None
    
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
                # parse_aih_tabular agora é um wrapper para parse_aih_simple
                extracted = parse_aih_tabular(pdf_bytes) 
                origem = "AIH"
            except Exception as e:
                import traceback
                st.error(f"Erro ao processar o PDF. Por favor, verifique o console do Streamlit Cloud para o Traceback completo.")
                st.code(traceback.format_exc(), language="python")
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
