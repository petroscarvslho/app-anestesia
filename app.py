import io
import re
from datetime import datetime, date, time

import streamlit as st

# PDF
import fitz  # PyMuPDF

# ======================================================
# CONFIG (mobile-first) + CSS leve
# ======================================================
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")
st.markdown(
    """
    <style>
      .block-container {max-width: 760px !important; padding-top: 1rem;}
      h1,h2 { letter-spacing:-.3px }
      .badge{display:inline-flex;gap:.4rem;align-items:center;padding:.15rem .5rem;border:1px solid #dbeafe;background:#eef2ff;border-radius:999px;font-size:.8rem}
      .dot{width:.55rem;height:.55rem;border-radius:50%}
      .aih{background:#3b82f6}.ocr{background:#22c55e}.man{background:#94a3b8}
      .stTextInput input, .stTextArea textarea { font-size:16px !important } /* evita zoom iOS */
      [data-testid="stHorizontalBlock"] { row-gap:.4rem }
    </style>
    """,
    unsafe_allow_html=True,
)

# ======================================================
# TABELAS/CONSTANTES
# ======================================================
HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# ======================================================
# HELPERS DE LIMPEZA / PARSE
# ======================================================
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
    chk = (linha or "").lower()
    chaves = [
        "nome do paciente", "nome da mãe", "nome da genitora",
        "cns", "cartão sus", "data de nasc", "sexo",
        "raça", "raça/cor", "telefone", "telefone de contato",
        "nº. prontuário", "nº prontuário", "prontuário",
        "endereço residencial", "endereço completo",
        "município de referência", "município de referencia",
        "uf", "cep",
    ]
    return any(k in chk for k in chaves)

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
    lbl = label.lower()
    for i, ln in enumerate(lines):
        if lbl in ln.lower():
            # mesmo ln após ':'
            if ":" in ln:
                same = ln.split(":", 1)[1].strip()
                if same and not parece_rotulo(same):
                    cand = same
                else:
                    cand = None
            else:
                cand = None
            # próximas linhas
            if not cand:
                for j in range(1, max_ahead + 1):
                    if i + j >= len(lines):
                        break
                    cand = lines[i + j].strip()
                    if not cand or parece_rotulo(cand):
                        cand = None
                        continue
                    break
            if not cand:
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
    data = {
        # identificação
        "nome_paciente": "", "nome_genitora": "", "cartao_sus": "",
        "data_nascimento": "", "sexo": "", "raca": "",
        "telefone_paciente": "", "prontuario": "",
        # endereço
        "endereco_completo": "", "municipio_referencia": "", "uf": "", "cep": "",
        # estabelecimento
        "hospital": "Maternidade Frei Justo Venture",
        "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
        # tempo e clínicos
        "data": date.today(),
        "hora": datetime.now().time().replace(microsecond=0),
        "diagnostico": "", "peso": "",
        "antecedente_transfusional": "Não",
        "antecedentes_obstetricos": "Não",
        "modalidade_transfusao": "Rotina",
    }

    data["nome_paciente"]   = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]   = limpar_nome(pick_after(lines, "Nome da Mãe") or pick_after(lines, "Nome da Genitora"))
    data["cartao_sus"]      = so_digitos(pick_after(lines, "CNS", prefer_digits=True) or pick_after(lines, "Cartão SUS", prefer_digits=True))
    data["data_nascimento"] = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))

    sx = (pick_after(lines, "Sexo") or "").lower()
    data["sexo"] = "Feminino" if "fem" in sx else ("Masculino" if "mas" in sx else "")

    rc = pick_after(lines, "Raça/Cor") or pick_after(lines, "Raça") or ""
    data["raca"] = limpar_nome(rc).upper()

    tel = pick_after(lines, "Telefone", prefer_digits=True) or pick_after(lines, "Telefone de Contato", prefer_digits=True)
    tel = so_digitos(tel)
    if len(tel) >= 10:
        tel = re.sub(r"^(\d{2})(\d{4,5})(\d{4}).*$", r"(\1) \2-\3", tel)
    data["telefone_paciente"] = tel

    data["prontuario"] = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True))

    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência") or
                                               pick_after(lines, "Município de Referencia"))
    data["uf"] = (pick_after(lines, "UF") or "")[:2].upper()

    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    data["cep"] = cep[:8] if len(cep) >= 8 else ""

    end = pick_after(lines, "Endereço Residencial") or pick_after(lines, "Endereço completo")
    data["endereco_completo"] = end

    return data

# ======================================================
# PDF -> TEXTO (PyMuPDF)  (sem OCR)
# ======================================================
def get_page_lines(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    raw = page.get_text("text")
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines, raw

def extract_from_pdf(file):
    pdf_bytes = file.read()
    lines, raw = get_page_lines(pdf_bytes)
    parsed = parse_aih_from_text(lines)
    return parsed, raw

# ======================================================
# OCR OPCIONAL (leve). Se não existir, não quebra.
# ======================================================
def try_rapid_ocr(image_bytes: bytes):
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        ocr = RapidOCR()  # modelos embutidos no pacote
        result, _ = ocr(arr)
        txt = "\n".join([r[1] for r in result]) if result else ""
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in txt.splitlines()]
        lines = [ln for ln in lines if ln]
        return parse_aih_from_text(lines), txt
    except Exception as e:
        return {}, f"[OCR indisponível]: {e}"

def extract_from_image(file, usar_ocr: bool):
    b = file.read()
    if not usar_ocr:
        # sem OCR: apenas “texto colado” (abaixo) ou manual
        return {}, ""
    return try_rapid_ocr(b)

# ======================================================
# ESTADO INICIAL (NADA DE KeyError)
# ======================================================
DEFAULTS = {
    "nome_paciente": "", "nome_genitora": "", "cartao_sus": "", "data_nascimento": "",
    "sexo": "", "raca": "", "telefone_paciente": "", "prontuario": "",
    "endereco_completo": "", "municipio_referencia": "", "uf": "", "cep": "",
    "hospital": "Maternidade Frei Justo Venture",
    "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
    "data": date.today(),
    "hora": datetime.now().time().replace(microsecond=0),
    "diagnostico": "", "peso": "",
    "antecedente_transfusional": "Não",
    "antecedentes_obstetricos": "Não",
    "modalidade_transfusao": "Rotina",
}
if "dados" not in st.session_state:
    st.session_state.dados = DEFAULTS.copy()
if "origem" not in st.session_state:
    st.session_state.origem = {k: "MAN" for k in DEFAULTS}
if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""

def mark_origin_update(parsed: dict, origem: str):
    for k, v in parsed.items():
        if v not in (None, "", []):
            st.session_state.dados[k] = v
            st.session_state.origem[k] = origem

# ======================================================
# UI
# ======================================================
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption(
    'Envie **PDF da AIH** (preferível) ou **Foto**. Se a foto não reconhecer, cole o texto detectado abaixo e eu preencho igual. '
    'Origem dos dados: '
    '<span class="badge"><span class="dot aih"></span>AIH</span> '
    '<span class="badge"><span class="dot ocr"></span>OCR</span> '
    '<span class="badge"><span class="dot man"></span>Manual</span>',
    unsafe_allow_html=True,
)

# ---------- Upload ----------
st.subheader("1) Enviar arquivo")
col1, col2 = st.columns([1,1])
with col1:
    up = st.file_uploader(
        "PDF (AIH) ou imagem (JPG/PNG)",
        type=["pdf", "jpg", "jpeg", "png"],
        label_visibility="collapsed",
        accept_multiple_files=False,
    )
with col2:
    usar_ocr = st.toggle("Tentar OCR para fotos?", value=False, help="Desmarcado = sem OCR (recomendado para deixar leve).")

if up is not None:
    nome = up.name.lower()
    if nome.endswith(".pdf"):
        parsed, raw_txt = extract_from_pdf(up)
        mark_origin_update(parsed, "AIH")
        st.success("AIH lida. Campos preenchidos onde possível.")
        st.session_state.raw_text = raw_txt
    else:
        parsed, raw_txt = extract_from_image(up, usar_ocr)
        if parsed:
            mark_origin_update(parsed, "OCR")
            st.success("Foto reconhecida via OCR. Revise os campos.")
        else:
            st.warning("Não extraí nada da foto. Você pode colar o texto abaixo e eu preencho.")
        st.session_state.raw_text = raw_txt or st.session_state.raw_text

# ---------- Plano B: colar texto e parsear ----------
with st.expander("📋 Ou cole aqui o texto (de Live Text / Google Lens / scanner com OCR)"):
    texto = st.text_area("Texto da AIH (colar)", value="", height=180, placeholder="Cole aqui…")
    if st.button("Preencher a partir do texto colado"):
        linhas = [re.sub(r"\s+", " ", ln.strip()) for ln in (texto or "").splitlines()]
        linhas = [ln for ln in linhas if ln]
        parsed = parse_aih_from_text(linhas)
        mark_origin_update(parsed, "AIH")
        st.session_state.raw_text = texto
        st.success("Texto interpretado e campos atualizados.")

st.subheader("2) Revisar e completar")

def badgez(key):
    o = st.session_state.origem.get(key, "MAN")
    if o == "AIH":
        return "AIH"
    if o == "OCR":
        return "OCR"
    return "Manual"

# -------- Identificação
st.markdown("### Identificação do Paciente")
c1, c2 = st.columns(2)
with c1:
    st.text_input(f"Nome do Paciente · {badgez('nome_paciente')}", key="nome_paciente", value=st.session_state.dados["nome_paciente"])
with c2:
    st.text_input(f"Nome da Mãe · {badgez('nome_genitora')}", key="nome_genitora", value=st.session_state.dados["nome_genitora"])

c3, c4 = st.columns(2)
with c3:
    st.text_input(f"Cartão SUS (CNS) · {badgez('cartao_sus')}", key="cartao_sus", value=st.session_state.dados["cartao_sus"])
with c4:
    st.text_input(f"Data de Nascimento (DD/MM/AAAA) · {badgez('data_nascimento')}", key="data_nascimento", value=st.session_state.dados["data_nascimento"])

c5, c6 = st.columns(2)
with c5:
    st.radio("Sexo", ["Feminino", "Masculino"], key="sexo",
             index=0 if (st.session_state.dados.get("sexo") or "Feminino") == "Feminino" else 1)
with c6:
    op_raca = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
    atual = st.session_state.dados.get("raca", "PARDA")
    st.radio("Raça/Cor", op_raca, key="raca",
             index=op_raca.index(atual) if atual in op_raca else 2)

c7, c8 = st.columns(2)
with c7:
    st.text_input(f"Telefone do Paciente · {badgez('telefone_paciente')}", key="telefone_paciente", value=st.session_state.dados["telefone_paciente"])
with c8:
    st.text_input(f"Núm. Prontuário · {badgez('prontuario')}", key="prontuario", value=st.session_state.dados["prontuario"])

# -------- Endereço
st.markdown("### Endereço")
c9, c10 = st.columns([3,2])
with c9:
    st.text_input(f"Endereço completo · {badgez('endereco_completo')}", key="endereco_completo", value=st.session_state.dados["endereco_completo"])
with c10:
    st.text_input(f"Município de referência · {badgez('municipio_referencia')}", key="municipio_referencia", value=st.session_state.dados["municipio_referencia"])

c11, c12 = st.columns(2)
with c11:
    st.text_input(f"UF · {badgez('uf')}", key="uf", value=st.session_state.dados["uf"])
with c12:
    st.text_input(f"CEP · {badgez('cep')}", key="cep", value=st.session_state.dados["cep"])

# -------- Estabelecimento
st.markdown("### Estabelecimento")
ops_hosp = list(HOSPITAIS.keys())
h_idx = ops_hosp.index(st.session_state.dados["hospital"]) if st.session_state.dados["hospital"] in ops_hosp else 0
sel_hosp = st.selectbox("Hospital / Unidade de saúde", ops_hosp, index=h_idx)
st.session_state.dados["hospital"] = sel_hosp
tel_padrao = HOSPITAIS.get(sel_hosp, "")
tel_val = st.session_state.dados.get("telefone_unidade") or tel_padrao
tel_val = st.text_input("Telefone da Unidade (padrão, pode ajustar)", value=tel_val)
st.session_state.dados["telefone_unidade"] = tel_val

# -------- Data & Hora
st.markdown("### Data e Hora")
c13, c14 = st.columns(2)
with c13:
    st.session_state.dados["data"] = st.date_input("Data", value=st.session_state.dados["data"])
with c14:
    st.session_state.dados["hora"] = st.time_input("Hora", value=st.session_state.dados["hora"])

# -------- Clínicos
st.markdown("### Dados clínicos")
st.session_state.dados["diagnostico"] = st.text_input("Diagnóstico", value=st.session_state.dados["diagnostico"])
st.session_state.dados["peso"] = st.text_input("Peso (kg)", value=st.session_state.dados["peso"])
st.session_state.dados["antecedente_transfusional"] = st.radio("Antecedente Transfusional?", ["Não", "Sim"], index=0 if st.session_state.dados["antecedente_transfusional"]=="Não" else 1)
st.session_state.dados["antecedentes_obstetricos"] = st.radio("Antecedentes Obstétricos?", ["Não", "Sim"], index=0 if st.session_state.dados["antecedentes_obstetricos"]=="Não" else 1)
st.session_state.dados["modalidade_transfusao"] = st.radio("Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"],
                                                          index=["Rotina","Programada","Urgência","Emergência"].index(st.session_state.dados["modalidade_transfusao"]))

# -------- Gerar PDF simples (exemplo)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

def gerar_pdf_bytes(d):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 40
    c.setFont("Helvetica-Bold", 14); c.drawString(40, y, "Ficha HEMOBA - Resumo"); y -= 24
    c.setFont("Helvetica", 10)
    def line(lbl, val):
        nonlocal y
        c.drawString(40, y, f"{lbl}: {val}"); y -= 16
    # principais
    line("Paciente", d["nome_paciente"])
    line("Mãe", d["nome_genitora"])
    line("CNS", d["cartao_sus"])
    line("Nascimento", d["data_nascimento"])
    line("Sexo", d["sexo"]); line("Raça/Cor", d["raca"])
    line("Telefone", d["telefone_paciente"]); line("Prontuário", d["prontuario"])
    line("Endereço", d["endereco_completo"]); line("Município", d["municipio_referencia"])
    line("UF/CEP", f'{d["uf"]} / {d["cep"]}')
    line("Hospital", d["hospital"]); line("Telefone da Unidade", d["telefone_unidade"])
    line("Data/Hora", f'{d["data"].strftime("%d/%m/%Y")} {d["hora"].strftime("%H:%M")}')
    line("Diagnóstico", d["diagnostico"]); line("Peso (kg)", d["peso"])
    line("Antecedente Transfusional", d["antecedente_transfusional"])
    line("Antecedentes Obstétricos", d["antecedentes_obstetricos"])
    line("Modalidade", d["modalidade_transfusao"])
    c.showPage(); c.save()
    buf.seek(0)
    return buf

if st.button("Gerar PDF Final"):
    pdfbuf = gerar_pdf_bytes(st.session_state.dados)
    st.download_button("Baixar PDF", data=pdfbuf, file_name="ficha_hemo.pdf", mime="application/pdf")

# -------- Debug
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    st.text_area("Texto bruto", value=st.session_state.raw_text or "", height=220)
    snap = {**st.session_state.dados}
    st.json(snap)