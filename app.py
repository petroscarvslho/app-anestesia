# -*- coding: utf-8 -*-
import io
import re
from datetime import datetime, date, time

import streamlit as st

# ====== IMPORTS OPCIONAIS (nunca quebram o app) ===============================
# PDF: PyMuPDF
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

# OCR opcional via pytesseract (se a lib e o binário existirem no host)
try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    HAS_TESS = True
except Exception:
    HAS_TESS = False


# ====== CONFIG GERAL (mobile-first) + CSS =====================================
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")

st.markdown(
    """
<style>
/* container estreito = melhor no celular */
.block-container {max-width: 740px !important; padding-top: 1rem;}
/* tipografia */
h1, h2, h3 { letter-spacing: -0.2px; }
label { font-weight: 600; }
hr { border:none; height:1px; background:#eee; margin: 1.0rem 0; }
/* inputs 16px para evitar zoom no iOS */
.stTextInput > div > div > input,
.stTextArea textarea,
.stDateInput input,
.stTimeInput input { font-size: 16px !important; }

/* “bolinha” indicando origem do dado */
.field-aih label:before,
.field-ocr label:before {
  content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%;
  margin-right:.5rem; vertical-align:middle;
}
.field-aih label:before { background:#3b82f6; }  /* azul (AIH) */
.field-ocr label:before { background:#22c55e; }  /* verde (OCR) */

/* badge de legenda no caption */
.badge {display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem;
  padding:.15rem .5rem; border-radius:999px; background:#eef2ff; color:#111;
  border:1px solid #dbeafe; }
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih {background:#3b82f6;}
.dot-ocr {background:#22c55e;}
.dot-man {background:#cbd5e1;}
</style>
""",
    unsafe_allow_html=True,
)


# ====== CONSTANTES ============================================================
HOSPITAIS = {
    "Maternidade Frei Justo Venture": "(75) 3331-9400",
    "Hospital Regional da Chapada Diamantina": "(75) 3331-9900",
}

# ====== HELPERS ===============================================================
def so_digitos(txt: str) -> str:
    return re.sub(r"\D", "", txt or "")

def normaliza_data(txt: str) -> str:
    """Aceita 28/12/1987, 28-12-1987, 28.12.1987, 28 12 1987."""
    if not txt:
        return ""
    m = re.search(r"(\d{2})[^\d]?(\d{2})[^\d]?(\d{4})", txt)
    if not m:
        return ""
    d, mth, y = m.groups()
    return f"{d}/{mth}/{y}"

def limpar_nome(txt: str) -> str:
    if not txt:
        return ""
    # aceita letras acentuadas, espaços, apóstrofo, ponto e hífen
    partes = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]+", txt)
    val = " ".join(partes).strip()
    return re.sub(r"\s+", " ", val)

def parece_rotulo(linha: str) -> bool:
    if not linha:
        return False
    chk = linha.lower()
    chaves = [
        "nome do paciente","nome da mãe","nome da genitora","cns","cartão sus",
        "data de nasc","sexo","raça","raça/cor","município de referência","município de referencia",
        "endereço residencial","endereço completo","nº. prontuário","nº prontuário","prontuário",
        "uf","cep","telefone","telefone de contato","nome do estabelecimento solicitante",
        "nome do estabelecimento executante"
    ]
    return any(k in chk for k in chaves)


# ====== PARSER DO PDF (via PyMuPDF) ==========================================
def get_pdf_lines(pdf_bytes: bytes):
    """Extrai texto da 1ª página como linhas limpas."""
    if not HAS_FITZ:
        return [], ""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    raw = page.get_text("text")  # respeita quebras do formulário AIH
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines, raw

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
    """Acha um rótulo `label` e retorna o melhor valor logo após (na mesma linha ou até `max_ahead`)."""
    L = label.lower()
    for i, ln in enumerate(lines):
        if L in ln.lower():
            # tenta na mesma linha após ':'
            same = None
            if ":" in ln:
                same = ln.split(":", 1)[1].strip()
                if same and not parece_rotulo(same):
                    cand = same
                    if prefer_digits:
                        d = so_digitos(cand)
                        if len(d) >= 6:
                            return d
                    if prefer_date:
                        d = normaliza_data(cand)
                        if d:
                            return d
                    return cand
            # tenta nas próximas linhas
            for j in range(1, max_ahead + 1):
                if i + j >= len(lines): break
                cand = lines[i + j].strip()
                if not cand or parece_rotulo(cand):  # pula rótulos vazios/próximos
                    continue
                if prefer_digits:
                    d = so_digitos(cand)
                    if len(d) >= 6:
                        return d
                if prefer_date:
                    d = normaliza_data(cand)
                    if d:
                        return d
                return cand
    return ""

def parse_aih_from_lines(lines):
    """Monta o dicionário com os campos que queremos."""
    data = {
        # AIH / OCR
        "nome_paciente": "", "nome_genitora": "", "cartao_sus": "", "data_nascimento": "",
        "sexo": "", "raca": "", "telefone_paciente": "", "prontuario": "",
        "endereco_completo": "", "municipio_referencia": "", "uf": "", "cep": "",
        # Manuais / padrão
        "hospital": "Maternidade Frei Justo Venture",
        "telefone_unidade": HOSPITAIS["Maternidade Frei Justo Venture"],
        "data": date.today(),
        "hora": datetime.now().time().replace(microsecond=0),
        "diagnostico": "", "peso": "",
        "antecedente_transfusional": "Não",
        "antecedentes_obstetricos": "Não",
        "modalidade_transfusao": "Rotina",
    }

    # — campos
    data["nome_paciente"]        = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]        = limpar_nome(pick_after(lines, "Nome da Mãe") or pick_after(lines, "Nome da Genitora"))
    data["cartao_sus"]           = so_digitos(pick_after(lines, "CNS", prefer_digits=True) or pick_after(lines, "Cartão SUS", prefer_digits=True))
    data["data_nascimento"]      = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))

    sx = (pick_after(lines, "Sexo") or "").lower()
    if   "fem" in sx: data["sexo"] = "Feminino"
    elif "mas" in sx: data["sexo"] = "Masculino"

    rc = pick_after(lines, "Raça/Cor") or pick_after(lines, "Raça") or ""
    rc = limpar_nome(rc).upper()
    if rc in {"BRANCA","PRETA","PARDA","AMARELA","INDÍGENA","INDIGENA"}:
        data["raca"] = "INDÍGENA" if "NDIGEN" in rc else rc

    tel = pick_after(lines, "Telefone", prefer_digits=True) or pick_after(lines, "Telefone de Contato", prefer_digits=True)
    d = so_digitos(tel)
    if len(d) >= 10:
        data["telefone_paciente"] = re.sub(r"^(\d{2})(\d{4,5})(\d{4}).*$", r"(\1) \2-\3", d)

    data["prontuario"]           = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True) or pick_after(lines, "Núm. Prontuário", prefer_digits=True))

    data["endereco_completo"]    = pick_after(lines, "Endereço Residencial") or pick_after(lines, "Endereço completo")

    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência") or pick_after(lines, "Município de Referencia"))
    data["uf"]                   = (pick_after(lines, "UF") or "").strip()[:2].upper()

    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    data["cep"] = cep[:8] if len(cep) >= 8 else ""

    return data

def extract_from_pdf(uploaded_file):
    """Retorna (dados, raw_text). Nunca levanta exceção pra UI."""
    try:
        if not HAS_FITZ:
            return {}, ""
        pdf_bytes = uploaded_file.read()
        lines, raw = get_pdf_lines(pdf_bytes)
        if not lines:
            return {}, raw or ""
        parsed = parse_aih_from_lines(lines)
        return parsed, raw
    except Exception as e:
        st.info(f"Leitura de PDF indisponível: {e}")
        return {}, ""


# ====== OCR de IMAGEM (opcional, rápido) ======================================
def extract_from_image(uploaded_file):
    """Tenta OCR com pytesseract se existir. Retorna (dados, raw_text)."""
    try:
        if not HAS_TESS:
            return {}, ""
        img = Image.open(uploaded_file).convert("RGB")
        # configurações simples que costumam ajudar:
        txt = pytesseract.image_to_string(img, lang="por")  # se 'por' faltar, usa eng automático
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in txt.splitlines() if ln.strip()]
        parsed = parse_aih_from_lines(lines)
        return parsed, txt
    except Exception as e:
        st.info(f"OCR de imagem não disponível: {e}")
        return {}, ""


# ====== ESTADO ================================================================
def _estado_inicial():
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
    st.session_state.dados = _estado_inicial()
if "origem" not in st.session_state:
    st.session_state.origem = {k: "MAN" for k in st.session_state.dados}
if "raw_txt" not in st.session_state:
    st.session_state.raw_txt = ""


# ====== APLICA EXTRAÇÃO NO ESTADO ============================================
def aplicar_extracao(d: dict, origem: str):
    """Copia só campos não-vazios e marca origem por-campo."""
    for k, v in (d or {}).items():
        if v not in (None, "", []):
            st.session_state.dados[k] = v
            st.session_state.origem[k] = origem


# ====== UI ====================================================================
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption(
    'Envie a **AIH em PDF** (preferível) ou **foto (JPG/PNG)**. '
    'Os campos mostram a origem: '
    '<span class="badge"><span class="dot dot-aih"></span>AIH</span> '
    '<span class="badge"><span class="dot dot-ocr"></span>OCR</span> '
    '<span class="badge"><span class="dot dot-man"></span>Manual</span>.',
    unsafe_allow_html=True,
)

# 1) Upload
st.subheader("1) Enviar Ficha AIH (PDF) ou Foto")
up = st.file_uploader(
    "Arraste o PDF ou a foto (JPG/PNG)",
    type=["pdf", "jpg", "jpeg", "png"],
    label_visibility="collapsed",
)

if up is not None:
    name = (up.name or "").lower()
    is_pdf = name.endswith(".pdf")

    if is_pdf:
        dados, raw_txt = extract_from_pdf(up)
        origem = "AIH"
    else:
        dados, raw_txt = extract_from_image(up)
        origem = "OCR"

    if dados:
        aplicar_extracao(dados, origem)
        st.session_state.raw_txt = raw_txt or ""
        st.success("Dados extraídos! Revise e complete abaixo.")
    else:
        st.session_state.raw_txt = raw_txt or ""
        st.warning(
            "Não foi possível extrair automaticamente deste arquivo. "
            "Preencha manualmente abaixo."
        )

st.subheader("2) Revisar e completar formulário ↩︎")

def label_input(lbl: str, key: str):
    """Text input com marcador de origem (AIH/OCR/Manual)."""
    cls = "field-aih" if st.session_state.origem.get(key) == "AIH" else \
          "field-ocr" if st.session_state.origem.get(key) == "OCR" else ""
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
    st.text_input(lbl, key=key, value=st.session_state.dados.get(key, ""))
    st.markdown('</div>', unsafe_allow_html=True)

# --- Identificação
st.markdown("### Identificação do Paciente")
label_input("Nome do Paciente", "nome_paciente")
label_input("Nome da Mãe", "nome_genitora")
label_input("Cartão SUS (CNS)", "cartao_sus")
label_input("Data de Nascimento (DD/MM/AAAA)", "data_nascimento")

# Sexo
sexo_val = st.session_state.dados.get("sexo", "Feminino")
sexo_idx = 0 if sexo_val == "Feminino" else 1
cls = "field-aih" if st.session_state.origem.get("sexo") == "AIH" else \
      "field-ocr" if st.session_state.origem.get("sexo") == "OCR" else ""
st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
st.radio("Sexo", ["Feminino", "Masculino"], key="sexo", index=sexo_idx)
st.markdown('</div>', unsafe_allow_html=True)

# Raça/Cor
r_opts = ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"]
r_val = st.session_state.dados.get("raca", "PARDA")
r_idx = r_opts.index(r_val) if r_val in r_opts else 2
cls = "field-aih" if st.session_state.origem.get("raca") == "AIH" else \
      "field-ocr" if st.session_state.origem.get("raca") == "OCR" else ""
st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
st.radio("Raça/Cor", r_opts, key="raca", index=r_idx)
st.markdown('</div>', unsafe_allow_html=True)

label_input("Telefone do Paciente", "telefone_paciente")
label_input("Núm. Prontuário", "prontuario")

# --- Endereço
st.markdown("### Endereço")
label_input("Endereço completo", "endereco_completo")
label_input("Município de referência", "municipio_referencia")
label_input("UF", "uf")
label_input("CEP", "cep")

# --- Estabelecimento
st.markdown("### Estabelecimento (selecione)")
h_atual = st.session_state.dados.get("hospital", "Maternidade Frei Justo Venture")
novo_h = st.radio("🏥 Hospital / Unidade de saúde", list(HOSPITAIS.keys()), index=list(HOSPITAIS.keys()).index(h_atual) if h_atual in HOSPITAIS else 0)
# sincroniza hospital/telefone sem callback (evita erros de policies)
if novo_h != h_atual:
    st.session_state.dados["hospital"] = novo_h
    st.session_state.origem["hospital"] = st.session_state.origem.get("hospital", "MAN")
    st.session_state.dados["telefone_unidade"] = HOSPITAIS.get(novo_h, "")
label_input("☎️ Telefone da Unidade (padrão, pode ajustar)", "telefone_unidade")

# --- Data & Hora
st.markdown("### Data e Hora")
st.date_input("📅 Data", key="data", value=st.session_state.dados.get("data", date.today()))
st.time_input("⏰ Hora", key="hora", value=st.session_state.dados.get("hora", datetime.now().time().replace(microsecond=0)))

# --- Dados clínicos (manuais)
st.markdown("### Dados clínicos (manuais)")
st.text_input("🩺 Diagnóstico", key="diagnostico", value=st.session_state.dados.get("diagnostico", ""))
st.text_input("⚖️ Peso (kg)", key="peso", value=st.session_state.dados.get("peso", ""))

st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional",
         index=0 if st.session_state.dados.get("antecedente_transfusional","Não")=="Não" else 1)
st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos",
         index=0 if st.session_state.dados.get("antecedentes_obstetricos","Não")=="Não" else 1)
m_opts = ["Rotina","Programada","Urgência","Emergência"]
m_val  = st.session_state.dados.get("modalidade_transfusao","Rotina")
st.radio("✍️ Modalidade de Transfusão", m_opts, key="modalidade_transfusao",
         index=m_opts.index(m_val) if m_val in m_opts else 0)

# --- Botão final (geração de PDF será conectada aqui)
st.button("Gerar PDF Final", type="primary")

# ====== DEBUG / LOGS ==========================================================
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    st.text_area("Texto bruto", value=st.session_state.raw_txt, height=220)

    # snapshot → serializável
    snap = dict(st.session_state.dados)
    if isinstance(snap.get("data"), date):
        snap["data"] = snap["data"].strftime("%Y-%m-%d")
    if isinstance(snap.get("hora"), time):
        snap["hora"] = snap["hora"].strftime("%H:%M")

    st.json(snap)

    # downloads
    import json, csv
    # JSON
    json_bytes = io.BytesIO(json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8"))
    st.download_button("Baixar .json", data=json_bytes, file_name="extracao.json", mime="application/json")
    # CSV (1 linha)
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=list(snap.keys()))
    writer.writeheader()
    writer.writerow(snap)
    st.download_button("Baixar .csv", data=csv_buf.getvalue().encode("utf-8"), file_name="extracao.csv", mime="text/csv")