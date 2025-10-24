import io
import os
import re
from datetime import datetime, date, time

import streamlit as st
import fitz  # PyMuPDF

# ==========================================================
# CONFIG GERAL (mobile-first) + CSS
# ==========================================================
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")

MOBILE_CSS = """
<style>
.block-container {max-width: 740px !important; padding-top: 1rem;}
h1,h2 { letter-spacing: -0.2px; }
h3 { margin-top: 1.0rem; }
hr { border:none; height:1px; background:#eee; margin: 1rem 0;}
label {font-weight:600}
.stTextInput > div > div > input,
.stTextArea textarea { font-size: 16px !important; } /* evita zoom em iOS */

/* Badges de origem (AIH/OCR/Manual) */
.badge {display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem; padding:.15rem .5rem; border-radius:999px; background:#eef2ff; color:#1f2937; border:1px solid #dbeafe;}
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih {background:#3b82f6;}   /* azul */
.dot-ocr {background:#22c55e;}   /* verde */
.dot-man {background:#9ca3af;}   /* cinza */

/* cor no label quando preenchido por extração */
.field-aih label:before { content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%; background:#3b82f6; margin-right:.5rem; vertical-align:middle; }
.field-ocr label:before { content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%; background:#22c55e; margin-right:.5rem; vertical-align:middle; }
.section-card { border:1px solid #eee; border-radius:12px; padding:1rem; background:#fff; }
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# ==========================================================
# CONSTANTES / MAPAS
# ==========================================================
HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"  # se não existir, gera PDF simples (fallback)

HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# ==========================================================
# HELPERS DE LIMPEZA
# ==========================================================
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

# ==========================================================
# PDF → TEXTO (PyMuPDF) + PARSER ROBUSTO
# ==========================================================
def get_page_lines(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    raw = page.get_text("text")
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines, raw

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
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
            # senão, pega próximas linhas
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
        # manuais/padrões
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

    data["nome_paciente"]        = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]        = limpar_nome(pick_after(lines, "Nome da Mãe"))
    data["cartao_sus"]           = so_digitos(pick_after(lines, "CNS", prefer_digits=True))
    data["data_nascimento"]      = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))

    sx = pick_after(lines, "Sexo")
    if "fem" in sx.lower(): data["sexo"] = "Feminino"
    elif "mas" in sx.lower(): data["sexo"] = "Masculino"

    rc = pick_after(lines, "Raça") or pick_after(lines, "Raça/Cor")
    data["raca"] = limpar_nome(rc).upper() if rc else ""

    tel = pick_after(lines, "Telefone", prefer_digits=True) or pick_after(lines, "Telefone de Contato", prefer_digits=True)
    tel_fmt = re.sub(r"(\d{2})(\d{4,5})(\d{4})", r"(\1) \2-\3", so_digitos(tel)) if tel else ""
    data["telefone_paciente"] = tel_fmt

    data["prontuario"]           = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True))
    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência") or pick_after(lines, "Município de Referencia"))
    data["uf"]                   = (pick_after(lines, "UF") or "").strip()[:2].upper()

    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    data["cep"] = cep[:8] if len(cep) >= 8 else ""

    end = pick_after(lines, "Endereço Residencial") or pick_after(lines, "Endereço completo")
    data["endereco_completo"] = end

    return data

def extract_from_pdf(file):
    try:
        pdf_bytes = file.read()
        lines, raw = get_page_lines(pdf_bytes)
        parsed = parse_aih_from_text(lines)
        return parsed, raw
    except Exception as e:
        st.error(f"Falha ao ler PDF: {e}")
        return {}, ""

# ==========================================================
# OCR (opcional e leve): usa RapidOCR se instalado; senão ignora
# ==========================================================
def try_rapid_ocr(image_bytes: bytes):
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
        # só informa; não quebra
        st.info(f"OCR opcional indisponível: {e}")
        return {}, ""

def extract_from_image(file):
    return try_rapid_ocr(file.read())

# ==========================================================
# FORM / UI HELPERS
# ==========================================================
def field_container_class(key: str) -> str:
    origin = st.session_state.origem.get(key, "MAN")
    if origin == "AIH":
        return "field-aih"
    if origin == "OCR":
        return "field-ocr"
    return ""

def label_with_origin(lbl: str, key: str, input_kind="text", **kwargs):
    cls = field_container_class(key)
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
    if input_kind == "text":
        st.text_input(lbl, key=key, value=st.session_state.dados.get(key, ""), **kwargs)
    elif input_kind == "radio":
        # kwargs deve conter 'options' e opcionalmente 'index'
        st.radio(lbl, key=key, **kwargs)
    st.markdown('</div>', unsafe_allow_html=True)

def update_phone_when_hospital_changes():
    hosp = st.session_state.get("hospital", "")
    if hosp in HOSPITAIS:
        st.session_state["telefone_unidade"] = HOSPITAIS[hosp]

# ==========================================================
# ESTADO INICIAL
# ==========================================================
if "dados" not in st.session_state:
    st.session_state.dados = {
        "nome_paciente": "", "nome_genitora": "", "cartao_sus": "", "data_nascimento": "",
        "sexo": "", "raca": "", "telefone_paciente": "", "prontuario": "",
        "endereco_completo": "", "municipio_referencia": "", "uf": "", "cep": "",
        "hospital": "Maternidade Frei Justo Venture",
        "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
        "data": date.today(), "hora": datetime.now().time().replace(microsecond=0),
        "diagnostico": "", "peso": "",
        "antecedente_transfusional": "Não", "antecedentes_obstetricos": "Não",
        "modalidade_transfusao": "Rotina",
    }

if "origem" not in st.session_state:
    st.session_state.origem = {k: "MAN" for k in st.session_state.dados.keys()}

st.session_state.setdefault("raw_txt", "")

# ==========================================================
# UI — SEMPRE MOSTRA FORMULÁRIO. Upload apenas PREENCHE.
# ==========================================================
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption(
    'Envie **PDF da AIH** (preferencial) **ou foto** (JPG/PNG). '
    'A cor do marcador no rótulo indica a origem do valor: '
    '<span class="badge"><span class="dot dot-aih"></span>AIH</span> '
    '<span class="badge"><span class="dot dot-ocr"></span>OCR</span> '
    '<span class="badge"><span class="dot dot-man"></span>Manual</span>.',
    unsafe_allow_html=True
)

# UPLOAD (preenche depois — o formulário fica sempre visível)
with st.container():
    st.subheader("1) Enviar Ficha AIH (PDF) ou Foto (opcional)")
    up = st.file_uploader("Arraste o PDF ou a foto (JPG/PNG)",
                          type=["pdf", "jpg", "jpeg", "png"], label_visibility="collapsed")

    if up is not None:
        is_pdf = up.name.lower().endswith(".pdf")
        if is_pdf:
            dados, raw_txt = extract_from_pdf(up)
            origem = "AIH"
        else:
            dados, raw_txt = extract_from_image(up)
            origem = "OCR"

        if dados:
            for k, v in dados.items():
                if v not in (None, ""):
                    st.session_state.dados[k] = v
                    st.session_state.origem[k] = origem
            st.session_state.raw_txt = raw_txt or st.session_state.raw_txt
            st.success("Dados extraídos e aplicados ao formulário.")
        else:
            st.warning("Não foi possível extrair automaticamente. Preencha manualmente.")

st.subheader("2) Revisar e completar formulário")

# --------- IDENTIFICAÇÃO ----------
with st.container():
    st.markdown("### Identificação do Paciente")
    label_with_origin("Nome do Paciente", "nome_paciente")
    label_with_origin("Nome da Mãe", "nome_genitora")
    label_with_origin("Cartão SUS (CNS)", "cartao_sus")
    label_with_origin("Data de Nascimento (DD/MM/AAAA)", "data_nascimento")

    # Sexo
    sexo_atual = st.session_state.dados.get("sexo") or "Feminino"
    label_with_origin(
        "Sexo",
        "sexo",
        input_kind="radio",
        options=["Feminino", "Masculino"],
        index=0 if sexo_atual == "Feminino" else 1
    )
    # Raça/Cor
    r_opts = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
    r_atual = st.session_state.dados.get("raca") or "PARDA"
    label_with_origin(
        "Raça/Cor",
        "raca",
        input_kind="radio",
        options=r_opts,
        index=r_opts.index(r_atual) if r_atual in r_opts else 2
    )

    label_with_origin("Telefone do Paciente", "telefone_paciente")
    label_with_origin("Núm. Prontuário", "prontuario")

# --------- ENDEREÇO ----------
with st.container():
    st.markdown("### Endereço")
    label_with_origin("Endereço completo", "endereco_completo")
    label_with_origin("Município de referência", "municipio_referencia")
    label_with_origin("UF", "uf")
    label_with_origin("CEP", "cep")

# --------- ESTABELECIMENTO ----------
with st.container():
    st.markdown("### Estabelecimento (selecione)")
    hosp_default = st.session_state.dados.get("hospital", "Maternidade Frei Justo Venture")
    st.radio("🏥 Hospital / Unidade de saúde", list(HOSPITAIS.keys()), key="hospital",
             index=list(HOSPITAIS.keys()).index(hosp_default) if hosp_default in HOSPITAIS else 0,
             on_change=update_phone_when_hospital_changes)
    label_with_origin("☎️ Telefone da Unidade", "telefone_unidade")

# --------- DATA & HORA ----------
with st.container():
    st.markdown("### Data e Hora")
    st.date_input("📅 Data", key="data", value=st.session_state.dados.get("data", date.today()))
    st.time_input("⏰ Hora", key="hora", value=st.session_state.dados.get("hora", datetime.now().time().replace(microsecond=0)))

# --------- DADOS CLÍNICOS ----------
with st.container():
    st.markdown("### Dados clínicos (manuais)")
    label_with_origin("🩺 Diagnóstico", "diagnostico")
    label_with_origin("⚖️ Peso (kg)", "peso")
    st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional",
             index=0 if st.session_state.dados.get("antecedente_transfusional", "Não") == "Não" else 1)
    st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos",
             index=0 if st.session_state.dados.get("antecedentes_obstetricos", "Não") == "Não" else 1)
    st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], key="modalidade_transfusao",
             index=["Rotina", "Programada", "Urgência", "Emergência"].index(st.session_state.dados.get("modalidade_transfusao", "Rotina")))

# ==========================================================
# GERAÇÃO DE PDF (template se houver, senão fallback simples)
# ==========================================================
def generate_pdf_bytes(data_dict: dict) -> bytes:
    """
    1) Se existir um formulário 'modelo_hemo.pdf', tenta preencher com PyPDFForm.
    2) Se não existir (ou der erro), gera um PDF simples com ReportLab contendo os campos.
    Nunca levanta exceção para não derrubar o app.
    """
    # 1) Tentar com template
    try:
        if os.path.exists(HEMOBA_TEMPLATE_PATH):
            from PyPDFForm.wrapper import PdfWrapper
            # Mapeie aqui os nomes dos campos do seu PDF. Campos desconhecidos são ignorados pelo PyPDFForm.
            mapping = {
                "nome_paciente": data_dict.get("nome_paciente", ""),
                "nome_mae": data_dict.get("nome_genitora", ""),
                "cns": data_dict.get("cartao_sus", ""),
                "data_nasc": data_dict.get("data_nascimento", ""),
                "sexo": data_dict.get("sexo", ""),
                "raca": data_dict.get("raca", ""),
                "telefone_paciente": data_dict.get("telefone_paciente", ""),
                "prontuario": data_dict.get("prontuario", ""),
                "endereco": data_dict.get("endereco_completo", ""),
                "municipio": data_dict.get("municipio_referencia", ""),
                "uf": data_dict.get("uf", ""),
                "cep": data_dict.get("cep", ""),
                "hospital": data_dict.get("hospital", ""),
                "telefone_unidade": data_dict.get("telefone_unidade", ""),
                "data": str(data_dict.get("data", "")),
                "hora": str(data_dict.get("hora", "")),
                "diagnostico": data_dict.get("diagnostico", ""),
                "peso": data_dict.get("peso", ""),
                "antecedente_transfusional": data_dict.get("antecedente_transfusional", ""),
                "antecedentes_obstetricos": data_dict.get("antecedentes_obstetricos", ""),
                "modalidade_transfusao": data_dict.get("modalidade_transfusao", ""),
            }
            pdf = PdfWrapper(HEMOBA_TEMPLATE_PATH)
            pdf.fill(mapping, flatten=False)
            return pdf.read()
    except Exception as e:
        st.info(f"Não foi possível preencher o template: {e}. Gerando PDF simples...")

    # 2) Fallback: PDF simples com ReportLab
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import cm

        buf = io.BytesIO()
        cnv = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        x, y = 2*cm, h - 2.5*cm

        def draw_line(label, value):
            nonlocal y
            cnv.setFont("Helvetica-Bold", 10)
            cnv.drawString(x, y, f"{label}:")
            cnv.setFont("Helvetica", 10)
            cnv.drawString(x + 5.5*cm, y, str(value or ""))
            y -= 0.6*cm

        cnv.setFont("Helvetica-Bold", 14)
        cnv.drawString(x, y, "Ficha HEMOBA (Gerada)")
        y -= 1.0*cm

        fields = [
            ("Nome do Paciente", data_dict.get("nome_paciente")),
            ("Nome da Mãe", data_dict.get("nome_genitora")),
            ("CNS", data_dict.get("cartao_sus")),
            ("Data de Nascimento", data_dict.get("data_nascimento")),
            ("Sexo", data_dict.get("sexo")),
            ("Raça/Cor", data_dict.get("raca")),
            ("Telefone do Paciente", data_dict.get("telefone_paciente")),
            ("Prontuário", data_dict.get("prontuario")),
            ("Endereço", data_dict.get("endereco_completo")),
            ("Município", data_dict.get("municipio_referencia")),
            ("UF", data_dict.get("uf")),
            ("CEP", data_dict.get("cep")),
            ("Hospital/Unidade", data_dict.get("hospital")),
            ("Tel. Unidade", data_dict.get("telefone_unidade")),
            ("Data", data_dict.get("data")),
            ("Hora", data_dict.get("hora")),
            ("Diagnóstico", data_dict.get("diagnostico")),
            ("Peso", data_dict.get("peso")),
            ("Antecedente Transfusional?", data_dict.get("antecedente_transfusional")),
            ("Antecedentes Obstétricos?", data_dict.get("antecedentes_obstetricos")),
            ("Modalidade de Transfusão", data_dict.get("modalidade_transfusao")),
        ]

        for lbl, val in fields:
            if y < 2*cm:
                cnv.showPage()
                y = h - 2.5*cm
            draw_line(lbl, val)

        cnv.showPage()
        cnv.save()
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        st.error(f"Falha ao gerar PDF: {e}")
        return b""

if st.button("Gerar PDF Final", type="primary"):
    # snapshot limpo para PDF
    snap = dict(st.session_state.dados)
    # garantir strings em data/hora
    if isinstance(snap.get("data"), date):
        snap["data"] = snap["data"].strftime("%d/%m/%Y")
    if isinstance(snap.get("hora"), time):
        snap["hora"] = snap["hora"].strftime("%H:%M")

    pdf_bytes = generate_pdf_bytes(snap)
    if pdf_bytes:
        nome = st.session_state.dados.get("nome_paciente", "paciente").strip().replace(" ", "_") or "paciente"
        st.download_button(
            "⬇️ Baixar Ficha HEMOBA",
            data=pdf_bytes,
            file_name=f"HEMOBA_{nome}.pdf",
            mime="application/pdf",
        )
    else:
        st.warning("Não foi possível gerar o PDF neste momento.")

# ==========================================================
# DEBUG / LOGS
# ==========================================================
with st.expander("🐿️ Debug: ver texto extraído e snapshot dos dados"):
    st.text_area("Texto bruto (última extração)", value=st.session_state.get("raw_txt", ""), height=220)
    # snapshot dos dados (serializável)
    snap = dict(st.session_state.dados)
    dd = snap.get("data")
    hh = snap.get("hora")
    if isinstance(dd, date):
        snap["data"] = dd.strftime("%d/%m/%Y")
    if isinstance(hh, time):
        snap["hora"] = hh.strftime("%H:%M")

    st.json(snap)

    import json, csv
    buf_json = io.BytesIO(json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8"))
    st.download_button("Baixar .json", data=buf_json, file_name="extracao.json", mime="application/json")

    buf_csv = io.StringIO()
    writer = csv.DictWriter(buf_csv, fieldnames=list(snap.keys()))
    writer.writeheader()
    writer.writerow(snap)
    st.download_button("Baixar .csv", data=buf_csv.getvalue().encode("utf-8"), file_name="extracao.csv", mime="text/csv")