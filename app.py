import io
import re
from datetime import datetime, date, time

import streamlit as st
import fitz  # PyMuPDF

# -----------------------------------------------------------------------------
# CONFIG GERAL (mobile-first) + CSS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")

MOBILE_CSS = """
<style>
.block-container {max-width: 740px !important; padding-top: 1.1rem;}
h1,h2 { letter-spacing:-0.3px; }
h3 { margin-top: 1.0rem; }
.badge {display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem; padding:.15rem .5rem;
        border-radius:999px; background:#eef2ff; color:#1f2937; border:1px solid #dbeafe;}
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih{background:#3b82f6;} .dot-ocr{background:#22c55e;} .dot-man{background:#9ca3af;}
.stTextInput > div > div > input, .stTextArea textarea { font-size: 16px !important; }
label {font-weight:600}
.field-aih label:before, .field-ocr label:before { content:""; display:inline-block; width:.6rem; height:.6rem;
  border-radius:50%; margin-right:.5rem; vertical-align:middle;}
.field-aih label:before{background:#3b82f6;} .field-ocr label:before{background:#22c55e;}
hr { border:none; height:1px; background:#eee; margin: 1.0rem 0;}
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# CONSTANTES
# -----------------------------------------------------------------------------
HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# -----------------------------------------------------------------------------
# SESSION STATE (sempre inicializado)
# -----------------------------------------------------------------------------
DEFAULTS = {
    "nome_paciente": "", "nome_genitora": "", "cartao_sus": "", "data_nascimento": "",
    "sexo": "Feminino", "raca": "PARDA", "telefone_paciente": "", "prontuario": "",
    "endereco_completo": "", "municipio_referencia": "", "uf": "", "cep": "",
    "hospital": "Maternidade Frei Justo Venture",
    "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
    "data": date.today(), "hora": datetime.now().time().replace(microsecond=0),
    "diagnostico": "", "peso": "",
    "antecedente_transfusional": "Não", "antecedentes_obstetricos": "Não",
    "modalidade_transfusao": "Rotina",
}
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""
if "origem" not in st.session_state:
    st.session_state.origem = {k: "MAN" for k in DEFAULTS.keys()}
# espelha defaults em keys de widgets (nível topo)
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)
# controle de mudança de hospital
st.session_state.setdefault("_last_hosp", st.session_state["hospital"])

# -----------------------------------------------------------------------------
# HELPERS DE LIMPEZA / PARSE
# -----------------------------------------------------------------------------
def limpar_nome(txt: str) -> str:
    if not txt:
        return ""
    partes = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]+", txt)
    val = " ".join(partes).strip()
    return re.sub(r"\s+", " ", val)

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

def parece_rotulo(linha: str) -> bool:
    if not linha:
        return False
    chk = linha.lower()
    chaves = [
        "nome do paciente","nome da mãe","nome da genitora","cns","cartão sus",
        "data de nasc","sexo","raça","raça/cor","município de referência",
        "município de referencia","endereço residencial","endereço completo",
        "prontuário","nº prontuário","número do prontuário","uf","cep",
        "telefone","telefone de contato","nome do estabelecimento solicitante"
    ]
    return any(k in chk for k in chaves)

def get_page_lines(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    raw = page.get_text("text")
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines, raw

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
    lbl = label.lower()
    for i, ln in enumerate(lines):
        if lbl in ln.lower():
            # valor no mesmo ln após ':'
            if ":" in ln:
                same = ln.split(":", 1)[1].strip()
                if same and not parece_rotulo(same):
                    cand = same
                    if prefer_digits:
                        dig = so_digitos(cand)
                        if len(dig) >= 6:
                            return dig
                    if prefer_date:
                        dd = normaliza_data(cand)
                        if dd:
                            return dd
                    return cand
            # olha as próximas linhas
            for j in range(1, max_ahead + 1):
                if i + j >= len(lines):
                    break
                cand = lines[i + j].strip()
                if not cand or parece_rotulo(cand):
                    continue
                if prefer_digits:
                    dig = so_digitos(cand)
                    if len(dig) >= 6:
                        return dig
                if prefer_date:
                    dd = normaliza_data(cand)
                    if dd:
                        return dd
                return cand
    return ""

def parse_aih_from_text(lines):
    data = DEFAULTS.copy()

    data["nome_paciente"]        = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]        = limpar_nome(pick_after(lines, "Nome da Mãe") or pick_after(lines, "Nome da Genitora"))
    data["cartao_sus"]           = so_digitos(pick_after(lines, "CNS", prefer_digits=True) or pick_after(lines, "Cartão SUS", prefer_digits=True))
    data["data_nascimento"]      = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))

    sx = pick_after(lines, "Sexo")
    if "fem" in (sx or "").lower(): data["sexo"] = "Feminino"
    elif "mas" in (sx or "").lower(): data["sexo"] = "Masculino"

    rc = pick_after(lines, "Raça") or pick_after(lines, "Raça/Cor")
    data["raca"] = limpar_nome(rc).upper() if rc else data["raca"]

    tel = pick_after(lines, "Telefone", prefer_digits=True) or pick_after(lines, "Telefone de contato", prefer_digits=True)
    tel_fmt = so_digitos(tel)
    if len(tel_fmt) >= 10:
        data["telefone_paciente"] = re.sub(r"(\d{2})(\d{4,5})(\d{4})", r"(\1) \2-\3", tel_fmt)

    data["prontuario"]           = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True))
    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência") or pick_after(lines, "Município de Referencia"))
    data["uf"]                   = (pick_after(lines, "UF") or "").strip()[:2].upper()

    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    if len(cep) >= 8:
        data["cep"] = cep[:8]

    end = pick_after(lines, "Endereço Residencial") or pick_after(lines, "Endereço completo")
    data["endereco_completo"] = end or data["endereco_completo"]

    return data

def apply_autofill(parsed_dict, origem):
    """Joga valores extraídos para o session_state + marca origem por campo."""
    if not parsed_dict:
        return
    for k, v in parsed_dict.items():
        if v is None:
            continue
        # garante tipos corretos para date/time
        if k == "data" and isinstance(v, date):
            st.session_state[k] = v
        elif k == "hora" and isinstance(v, time):
            st.session_state[k] = v
        else:
            st.session_state[k] = v
        st.session_state.origem[k] = origem

# -----------------------------------------------------------------------------
# EXTRAÇÃO: PDF, IMAGEM (OCR opcional), ou TEXTO COLADO/ARQUIVO .txt
# -----------------------------------------------------------------------------
def extract_from_pdf(file):
    try:
        pdf_bytes = file.read()
        lines, raw = get_page_lines(pdf_bytes)
        parsed = parse_aih_from_text(lines)
        return parsed, raw
    except Exception as e:
        st.error(f"Falha ao ler PDF: {e}")
        return {}, ""

def try_rapid_ocr(image_bytes: bytes):
    """OCR leve SE a lib existir; caso contrário, não quebra o app."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        ocr = RapidOCR()
        result, _ = ocr(arr)
        txt = "\n".join([r[1] for r in result]) if result else ""
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in txt.splitlines()]
        lines = [ln for ln in lines if ln]
        parsed = parse_aih_from_text(lines)
        return parsed, txt
    except Exception as e:
        # não é erro do usuário — só informa e segue
        st.info(f"OCR opcional não disponível/instalado: {e}")
        return {}, ""

def extract_from_image(file):
    return try_rapid_ocr(file.read())

def extract_from_text(texto):
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in (texto or "").splitlines()]
    lines = [ln for ln in lines if ln]
    parsed = parse_aih_from_text(lines)
    return parsed

# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.title("🩸 Gerador Automático de Ficha HEMOBA")

st.caption(
    'Envie **PDF** (preferencial), **foto** (OCR opcional) ou **.txt** com o conteúdo. '
    'Você também pode **colar texto** e clicar em “Aplicar texto”. '
    'Os pontos mostram a origem: '
    '<span class="badge"><span class="dot dot-aih"></span>AIH/TXT</span> '
    '<span class="badge"><span class="dot dot-ocr"></span>OCR</span> '
    '<span class="badge"><span class="dot dot-man"></span>Manual</span>.', unsafe_allow_html=True
)

with st.container(border=True):
    st.subheader("1) Enviar arquivo (PDF, JPG/PNG, TXT)")
    up = st.file_uploader(
        "Arraste o arquivo aqui (PDF, JPG/PNG ou TXT)", 
        type=["pdf", "jpg", "jpeg", "png", "txt"], 
        label_visibility="visible"
    )
    if up is not None:
        name = up.name.lower()
        origem = "AIH"
        dados, raw_txt = {}, ""
        if name.endswith(".pdf"):
            dados, raw_txt = extract_from_pdf(up)
            origem = "AIH"
        elif name.endswith(".txt"):
            raw_txt = up.read().decode("utf-8", errors="ignore")
            dados = extract_from_text(raw_txt)
            origem = "AIH"  # trata .txt como texto da AIH
        else:
            dados, raw_txt = extract_from_image(up)
            origem = "OCR" if dados else "MAN"
        # aplica
        if dados:
            apply_autofill(dados, origem)
            st.session_state.raw_text = raw_txt
            st.success("Dados extraídos! Revise e complete abaixo.")
        else:
            st.session_state.raw_text = raw_txt or st.session_state.raw_text
            st.warning("Não foi possível extrair automaticamente. Você pode colar o texto abaixo e aplicar.")

with st.container(border=True):
    st.subheader("2) Colar texto (opcional, rápido)")
    txt = st.text_area("Texto bruto (cole aqui se preferir)", st.session_state.raw_text or "", height=180)
    col_a, col_b = st.columns([1,1])
    with col_a:
        if st.button("Aplicar texto"):
            parsed = extract_from_text(txt)
            if parsed:
                apply_autofill(parsed, "AIH")
                st.session_state.raw_text = txt
                st.success("Texto aplicado com sucesso.")
            else:
                st.warning("Não identifiquei campos nesse texto. Revise o conteúdo colado.")
    with col_b:
        if st.button("Limpar texto"):
            st.session_state.raw_text = ""
            st.rerun()

st.subheader("3) Revisar e completar formulário")

def badge_for(key):
    src = st.session_state.origem.get(key, "MAN")
    if src == "AIH":
        return "field-aih"
    if src == "OCR":
        return "field-ocr"
    return ""

# --- Identificação
with st.container(border=True):
    st.markdown("### Identificação do Paciente")
    for lbl, key in [
        ("Nome do Paciente", "nome_paciente"),
        ("Nome da Mãe", "nome_genitora"),
        ("Cartão SUS (CNS)", "cartao_sus"),
        ("Data de Nascimento (DD/MM/AAAA)", "data_nascimento"),
    ]:
        st.markdown(f'<div class="{badge_for(key)}">', unsafe_allow_html=True)
        st.text_input(lbl, key=key)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="{badge_for("sexo")}">', unsafe_allow_html=True)
    st.radio("Sexo", ["Feminino", "Masculino"], key="sexo", horizontal=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="{badge_for("raca")}">', unsafe_allow_html=True)
    st.radio("Raça/Cor", ["BRANCA","PRETA","PARDA","AMARELA","INDÍGENA"], key="raca", horizontal=True)
    st.markdown('</div>', unsafe_allow_html=True)

    for lbl, key in [
        ("Telefone do Paciente", "telefone_paciente"),
        ("Núm. Prontuário", "prontuario"),
    ]:
        st.markdown(f'<div class="{badge_for(key)}">', unsafe_allow_html=True)
        st.text_input(lbl, key=key)
        st.markdown('</div>', unsafe_allow_html=True)

# --- Endereço
with st.container(border=True):
    st.markdown("### Endereço")
    for lbl, key in [
        ("Endereço completo", "endereco_completo"),
        ("Município de referência", "municipio_referencia"),
        ("UF", "uf"),
        ("CEP", "cep"),
    ]:
        st.markdown(f'<div class="{badge_for(key)}">', unsafe_allow_html=True)
        st.text_input(lbl, key=key)
        st.markdown('</div>', unsafe_allow_html=True)

# --- Estabelecimento
with st.container(border=True):
    st.markdown("### Estabelecimento")
    st.radio("🏥 Hospital / Unidade de saúde", list(HOSPITAIS.keys()), key="hospital")
    # se hospital mudou, atualiza telefone padrão
    if st.session_state["_last_hosp"] != st.session_state["hospital"]:
        st.session_state["telefone_unidade"] = HOSPITAIS[st.session_state["hospital"]]
        st.session_state["_last_hosp"] = st.session_state["hospital"]
    st.text_input("☎️ Telefone da Unidade (padrão, pode ajustar)", key="telefone_unidade")

# --- Data e Hora
with st.container(border=True):
    st.markdown("### Data e Hora")
    st.date_input("📅 Data", key="data", value=st.session_state.get("data", date.today()))
    st.time_input("⏰ Hora", key="hora", value=st.session_state.get("hora", datetime.now().time().replace(microsecond=0)))

# --- Dados clínicos
with st.container(border=True):
    st.markdown("### Dados clínicos (manuais)")
    st.text_input("🩺 Diagnóstico", key="diagnostico")
    st.text_input("⚖️ Peso (kg)", key="peso")
    st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional", horizontal=True)
    st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos", horizontal=True)
    st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], key="modalidade_transfusao", horizontal=True)

# --- Ação Final (geração do PDF ainda como placeholder)
st.button("Gerar PDF Final", type="primary")

# --- DEBUG
with st.expander("🐿️ Debug: ver texto extraído e baixar JSON/CSV"):
    st.text_area("Texto bruto extraído", st.session_state.raw_text or "", height=220, key="__debug_txtarea")
    # snapshot atual
    campos = list(DEFAULTS.keys())
    snap = {k: st.session_state.get(k) for k in campos}
    st.json(snap)
    import json, csv
    buf_json = io.BytesIO(json.dumps(snap, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
    st.download_button("Baixar JSON", data=buf_json, file_name="extracao.json", mime="application/json")

    buf_csv = io.StringIO()
    writer = csv.DictWriter(buf_csv, fieldnames=campos)
    writer.writeheader()
    writer.writerow({k: str(snap.get(k) or "") for k in campos})
    st.download_button("Baixar CSV", data=buf_csv.getvalue().encode("utf-8"), file_name="extracao.csv", mime="text/csv")