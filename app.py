import io
import re
from datetime import datetime, date, time

import streamlit as st
import fitz  # PyMuPDF

# ----------------------------------------------------------
# CONFIG (mobile-first) + CSS
# ----------------------------------------------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")
MOBILE_CSS = """
<style>
.block-container {max-width: 760px !important; padding-top: 1.0rem;}
h1,h2 { letter-spacing: -0.3px; margin-bottom:.3rem; }
h3 { margin-top: 1.0rem; }
.badge {display:inline-flex; align-items:center; gap:.35rem; font-size:.78rem; padding:.12rem .48rem; border-radius:999px; background:#eef2ff; color:#1e293b; border:1px solid #dbeafe;}
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih {background:#3b82f6;}   /* azul */
.dot-ocr {background:#22c55e;}   /* verde */
.dot-man {background:#cbd5e1;}   /* cinza */
.stTextInput > div > div > input,
.stTextArea textarea { font-size: 16px !important; } /* evita zoom no iOS */
label {font-weight:600}
hr { border:none; height:1px; background:#eee; margin: 1rem 0;}
.field-aih label:before, .field-ocr label:before, .field-man label:before {
  content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%;
  margin-right:.5rem; vertical-align:middle;
}
.field-aih label:before { background:#3b82f6;}
.field-ocr label:before { background:#22c55e;}
.field-man label:before { background:#94a3b8;}
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# ----------------------------------------------------------
# CONSTANTES / MAPAS
# ----------------------------------------------------------
HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# ----------------------------------------------------------
# HELPERs
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

def parece_rotulo(linha: str) -> bool:
    if not linha:
        return False
    chk = linha.lower()
    chaves = [
        "nome do paciente", "nome da mãe", "nome da genitora", "cns", "cartão sus",
        "data de nasc", "sexo", "raça", "raça/cor", "município de referência",
        "município de referencia", "endereço residencial", "endereço completo",
        "nº. prontuário", "num. prontuário", "número do prontuário", "nº prontuário",
        "prontuário", "uf", "cep", "telefone", "telefone de contato",
        "nome do estabelecimento solicitante"
    ]
    return any(k in chk for k in chaves)

# ----------------------------------------------------------
# PDF → TEXTO → CAMPOS
# ----------------------------------------------------------
def get_page_lines(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    raw = page.get_text("text")  # melhor preserva linhas para esse layout
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines, raw

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
    """Procura um rótulo (label) e retorna a melhor linha seguinte (até max_ahead)."""
    label_norm = label.lower()
    for i, ln in enumerate(lines):
        if label_norm in ln.lower():
            # tenta pegar no mesmo ln após ':'
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
            # senão, olha próximas linhas
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

def parse_aih_from_lines(lines):
    data = default_dados()

    data["nome_paciente"]        = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]        = limpar_nome(pick_after(lines, "Nome da Mãe"))
    data["cartao_sus"]           = so_digitos(pick_after(lines, "CNS", prefer_digits=True))
    data["data_nascimento"]      = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))

    sx = pick_after(lines, "Sexo")
    if "fem" in sx.lower():
        data["sexo"] = "Feminino"
    elif "mas" in sx.lower():
        data["sexo"] = "Masculino"

    rc = pick_after(lines, "Raça") or pick_after(lines, "Raça/Cor")
    data["raca"] = limpar_nome(rc).upper() if rc else ""

    tel = pick_after(lines, "Telefone", prefer_digits=True) or pick_after(lines, "Telefone de Contato", prefer_digits=True)
    if tel:
        tel_d = so_digitos(tel)
        data["telefone_paciente"] = re.sub(r"(\d{2})(\d{4,5})(\d{4})", r"(\1) \2-\3", tel_d) if len(tel_d) >= 10 else tel

    data["prontuario"]           = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True))
    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência") or pick_after(lines, "Município de Referencia"))
    data["uf"]                   = (pick_after(lines, "UF") or "").strip()[:2].upper()

    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    data["cep"] = cep[:8] if len(cep) >= 8 else ""

    end = pick_after(lines, "Endereço Residencial") or pick_after(lines, "Endereço completo")
    data["endereco_completo"] = end

    return data

def extract_from_pdf(uploaded_file):
    pdf_bytes = uploaded_file.read()
    lines, raw = get_page_lines(pdf_bytes)
    parsed = parse_aih_from_lines(lines)
    return parsed, raw

# ----------------------------------------------------------
# OCR (opcional): RapidOCR. Se não estiver instalado, ignora.
# ----------------------------------------------------------
def try_rapid_ocr(image_bytes: bytes):
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        ocr = RapidOCR()  # modelos embarcados (sem baixar na nuvem)
        result, _ = ocr(arr)
        txt = "\n".join([r[1] for r in result]) if result else ""
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in txt.splitlines()]
        lines = [ln for ln in lines if ln]
        parsed = parse_aih_from_lines(lines)
        return parsed, txt
    except Exception as e:
        # não quebra o app
        return {}, f"OCR não disponível: {e}"

def extract_from_image(uploaded_file):
    bytes_ = uploaded_file.read()
    parsed, text = try_rapid_ocr(bytes_)
    return parsed, text

# ----------------------------------------------------------
# ESTADO INICIAL (evita KeyError)
# ----------------------------------------------------------
def default_dados():
    return {
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

if "dados" not in st.session_state:
    st.session_state.dados = default_dados()

if "origem" not in st.session_state:
    st.session_state.origem = {k: "MAN" for k in st.session_state.dados.keys()}

if "raw_text" not in st.session_state:
    st.session_state.raw_text = ""

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption(
    'Envie **PDF da AIH** (preferencial) **ou foto** (JPG/PNG). A cor do marcador no rótulo indica a origem do valor: '
    '<span class="badge"><span class="dot dot-aih"></span>AIH</span> '
    '<span class="badge"><span class="dot dot-ocr"></span>OCR</span> '
    '<span class="badge"><span class="dot dot-man"></span>Manual</span>.',
    unsafe_allow_html=True
)

# 1) Upload
st.subheader("1) Enviar Ficha AIH (PDF) ou Foto (opcional) ↩︎")
up = st.file_uploader("Arraste o PDF ou a foto (JPG/PNG)", type=["pdf", "jpg", "jpeg", "png"], label_visibility="collapsed")

if up is not None:
    is_pdf = up.name.lower().endswith(".pdf")
    if is_pdf:
        try:
            dados, raw_txt = extract_from_pdf(up)
            origem = "AIH"
        except Exception as e:
            dados, raw_txt, origem = {}, f"Falha ao ler PDF: {e}", "MAN"
    else:
        dados, raw_txt = extract_from_image(up)
        origem = "OCR" if dados else "MAN"

    # aplica no estado
    if dados:
        for k, v in dados.items():
            if v not in (None, ""):
                st.session_state.dados[k] = v
                st.session_state.origem[k] = origem
        st.success("Dados extraídos e aplicados ao formulário.")
    else:
        st.warning("Não foi possível extrair automaticamente. Preencha manualmente abaixo.")

    st.session_state.raw_text = raw_txt or ""

# 2) Formulário
st.subheader("2) Revisar e completar formulário")

def field_class(key):
    o = st.session_state.origem.get(key, "MAN")
    return "field-aih" if o == "AIH" else ("field-ocr" if o == "OCR" else "field-man")

def input_text(label, key):
    st.markdown(f'<div class="{field_class(key)}">', unsafe_allow_html=True)
    st.text_input(label, key=key, value=st.session_state.dados.get(key, ""))
    st.markdown('</div>', unsafe_allow_html=True)

# Identificação
st.markdown("### Identificação do Paciente")
input_text("Nome do Paciente", "nome_paciente")
input_text("Nome da Mãe", "nome_genitora")
input_text("Cartão SUS (CNS)", "cartao_sus")
input_text("Data de Nascimento (DD/MM/AAAA)", "data_nascimento")

st.markdown(f'<div class="{field_class("sexo")}">', unsafe_allow_html=True)
st.radio("Sexo", ["Feminino", "Masculino"], key="sexo",
         index=0 if st.session_state.dados.get("sexo","Feminino")=="Feminino" else 1)
st.markdown('</div>', unsafe_allow_html=True)

st.markdown(f'<div class="{field_class("raca")}">', unsafe_allow_html=True)
op_raca = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
r_default = st.session_state.dados.get("raca","PARDA")
st.radio("Raça/Cor", op_raca,
         key="raca",
         index=op_raca.index(r_default) if r_default in op_raca else 2)
st.markdown('</div>', unsafe_allow_html=True)

input_text("Telefone do Paciente", "telefone_paciente")
input_text("Núm. Prontuário", "prontuario")

# Endereço
st.markdown("### Endereço")
input_text("Endereço completo", "endereco_completo")
input_text("Município de referência", "municipio_referencia")
input_text("UF", "uf")
input_text("CEP", "cep")

# Estabelecimento
def update_phone_when_hospital_changes():
    hosp = st.session_state.get("hospital", "")
    if hosp in HOSPITAIS:
        st.session_state["telefone_unidade"] = HOSPITAIS[hosp]

st.markdown("### Estabelecimento (selecione)")
st.radio("🏥 Hospital / Unidade de saúde", list(HOSPITAIS.keys()), key="hospital",
         index=list(HOSPITAIS.keys()).index(st.session_state.dados.get("hospital","Maternidade Frei Justo Venture")),
         on_change=update_phone_when_hospital_changes)
st.text_input("☎️ Telefone da Unidade (padrão, pode ajustar)", key="telefone_unidade",
              value=st.session_state.dados.get("telefone_unidade", HOSPITAIS["Maternidade Frei Justo Venture"]))

# Data & Hora
st.markdown("### Data e Hora")
st.date_input("📅 Data", key="data", value=st.session_state.dados.get("data", date.today()))
st.time_input("⏰ Hora", key="hora", value=st.session_state.dados.get("hora", datetime.now().time().replace(microsecond=0)))

# Dados clínicos
st.markdown("### Dados clínicos (manuais)")
st.text_input("🩺 Diagnóstico", key="diagnostico", value=st.session_state.dados.get("diagnostico",""))
st.text_input("⚖️ Peso (kg)", key="peso", value=st.session_state.dados.get("peso",""))
st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional",
         index=0 if st.session_state.dados.get("antecedente_transfusional","Não")=="Não" else 1)
st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos",
         index=0 if st.session_state.dados.get("antecedentes_obstetricos","Não")=="Não" else 1)
st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], key="modalidade_transfusao",
         index=["Rotina", "Programada", "Urgência", "Emergência"].index(st.session_state.dados.get("modalidade_transfusao","Rotina")))

# Sincroniza edits dos widgets -> st.session_state.dados
for k in list(st.session_state.dados.keys()):
    if k in st.session_state:
        st.session_state.dados[k] = st.session_state[k]

# BOTÃO PDF (placeholder: aqui você chama o gerador baseado no template)
st.button("Gerar PDF Final", type="primary")

# ----------------------------------------------------------
# DEBUG / LOGs
# ----------------------------------------------------------
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    st.text_area("Texto bruto", value=st.session_state.raw_text or "", height=220)
    # Snapshot JSON-friendly
    snap = {}
    for k, v in st.session_state.dados.items():
        if isinstance(v, (date, datetime, time)):
            try:
                snap[k] = v.isoformat()
            except Exception:
                snap[k] = str(v)
        else:
            snap[k] = v
    st.json(snap)

    # Downloads
    import json, csv
    buf_json = io.BytesIO(json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8"))
    st.download_button("Baixar .json", data=buf_json, file_name="extracao.json", mime="application/json")

    buf_csv = io.StringIO()
    writer = csv.DictWriter(buf_csv, fieldnames=list(snap.keys()))
    writer.writeheader()
    writer.writerow(snap)
    st.download_button("Baixar .csv", data=buf_csv.getvalue().encode("utf-8"),
                       file_name="extracao.csv", mime="text/csv")