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
    # Remove caracteres que não são letras, espaços, apóstrofos ou hífens
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
# EXTRAÇÃO SIMPLIFICADA (MAIS ROBUSTA)
# ----------------------------------------------------------
def parse_aih_simple(pdf_bytes: bytes):
    """Parser simplificado que extrai todo o texto e usa regex"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        # Extrai o texto de todas as páginas
        text += page.get_text("text")
    
    # Versão com quebra de linha para visualização
    full_text = text.strip() 
    # Versão limpa (sem quebras de linha e múltiplos espaços) para o regex
    clean_text = re.sub(r"\s+", " ", full_text).strip()
    
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
    
    # Padrões Regex (mais tolerantes a espaços e pontuações)
    # Grupo 1: O valor que queremos extrair
    patterns = {
        "nome_paciente": r"Nome\s+do\s+Paciente\s*[:\s]*(.*?)(?:Nome\s+da\s+Mãe|CNS|Prontuário|$)",
        "nome_genitora": r"Nome\s+da\s+(Mãe|Mae)\s*[:\s]*(.*?)(?:CNS|Prontuário|Data\s+de\s+Nasc|$)",
        "cartao_sus": r"CNS\s*[/\s]*Cartão\s+SUS\s*[:\s]*(\d+)",
        "data_nascimento": r"Data\s+de\s+Nasc\s*[:\s]*(\d{2}[^\d]?\d{2}[^\d]?\d{4})",
        "sexo": r"Sexo\s*[:\s]*(Feminino|Masculino|F|M)",
        "raca": r"Raça\s*[/\s]*Cor\s*[:\s]*(.*?)(?:Telefone|Prontuário|$)",
        "telefone_paciente": r"Telefone\s*[:\s]*([\d\s\-\(\)]+)",
        "prontuario": r"Prontuário\s*[:\s]*(\d+)",
        "endereco_completo": r"Endereço\s+Completo\s*[:\s]*(.*?)(?:Município|UF|CEP|$)",
        "municipio_referencia": r"Município\s*[:\s]*(.*?)(?:UF|CEP|$)",
        "uf": r"UF\s*[:\s]*([A-Z]{2})",
        "cep": r"CEP\s*[:\s]*(\d{5}[-\s]?\d{3})",
        "diagnostico": r"Diagnóstico\s*[:\s]*(.*?)(?:Peso|Antecedente|$)",
        "peso": r"Peso\s*\([Kk]g\)\s*[:\s]*([\d\.,]+)",
    }
    
    for field, pattern in patterns.items():
        match = re.search(pattern, clean_text, re.IGNORECASE | re.DOTALL)
        if match:
            value = match.group(1).strip()
            
            if field in ["nome_paciente", "nome_genitora", "municipio_referencia", "raca"]:
                data[field] = limpar_nome(value)
            elif field == "cartao_sus" or field == "prontuario":
                data[field] = so_digitos(value)
            elif field == "data_nascimento":
                data["data_nascimento"] = normaliza_data(value)
            elif field == "telefone_paciente":
                data["telefone_paciente"] = normaliza_telefone(value)
            elif field == "sexo":
                if re.search(r"fem|f", value, re.IGNORECASE):
                    data["sexo"] = "Feminino"
                elif re.search(r"masc|m", value, re.IGNORECASE):
                    data["sexo"] = "Masculino"
            elif field == "uf":
                data["uf"] = value.upper()
            elif field == "cep":
                data["cep"] = so_digitos(value)
            elif field == "peso":
                data["peso"] = value.replace(",", ".")
            else:
                data[field] = value
    
    # Retorna os dados extraídos e o texto completo para debug
    return data, full_text

# ----------------------------------------------------------
# EXTRAÇÃO COM OCR
# ----------------------------------------------------------
def try_rapid_ocr(img_bytes: bytes):
    """Tenta extrair dados usando OCR"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        
        ocr = RapidOCR()
        
        # O RapidOCR espera um caminho de arquivo ou bytes
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
        
        return data, ocr_text # Retorna o texto OCR para debug
    
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
if "full_text_debug" not in st.session_state:
    st.session_state.full_text_debug = "Nenhum texto extraído ainda."

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
        extracted = {}
        origem = "Erro"
        
        if "pdf" in file_type:
            try:
                pdf_bytes = uploaded.read()
                extracted, full_text = parse_aih_simple(pdf_bytes)
                origem = "AIH"
                st.session_state.full_text_debug = full_text
            except Exception as e:
                st.error(f"Erro ao processar PDF. Detalhes: {e}")
                st.exception(e)
                st.session_state.full_text_debug = f"ERRO: {e}\n{traceback.format_exc()}"
        else:
            img_bytes = uploaded.read()
            extracted, ocr_output = try_rapid_ocr(img_bytes)
            if isinstance(ocr_output, str) and ocr_output.startswith("Erro"):
                st.error(ocr_output)
                st.session_state.full_text_debug = ocr_output
            else:
                origem = "OCR"
                st.session_state.full_text_debug = ocr_output

        # Atualizar apenas campos não vazios
        for key, value in extracted.items():
            if value and value != "":
                st.session_state.dados[key] = value
                st.session_state.origem_dados[key] = origem
    
    if origem != "Erro":
        st.success(f"✅ Dados extraídos com sucesso via {origem}!")
    
    # CORREÇÃO: Forçar o Streamlit a recriar os widgets do formulário
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
    st.markdown("#### Texto Completo Extraído do PDF/OCR (Para Debug)")
    st.code(st.session_state.full_text_debug, language="text")
    st.markdown("#### JSON dos Itens Extraídos")
    st.json(st.session_state.dados)
    st.json(st.session_state.origem_dados)
