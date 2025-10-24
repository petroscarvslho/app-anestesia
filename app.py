import io
import re
from datetime import datetime, date, time

import streamlit as st
import fitz  # PyMuPDF

# ----------------------------------------------------------
# CONFIG GERAL (mobile-first) + CSS
# ----------------------------------------------------------
st.set_page_config(page_title="Gerador de Ficha HEMOBA", layout="centered")
MOBILE_CSS = """
<style>
/* largura máxima amigável pra celular */
.block-container {max-width: 740px !important; padding-top: 1.2rem;}
/* títulos */
h1,h2 { letter-spacing: -0.3px; }
h3 { margin-top: 1.2rem; }
/* badges de origem dos dados (AIH/OCR/Manual) */
.badge {display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem; padding:.15rem .5rem; border-radius:999px; background:#eef2ff; color:#274 ; border:1px solid #dbeafe;}
.badge .dot {width:.55rem;height:.55rem;border-radius:50%;}
.dot-aih {background:#3b82f6;}   /* azul */
.dot-ocr {background:#22c55e;}   /* verde */
.dot-man {background:#cbd5e1;}   /* cinza */
.stTextInput > div > div > input,
.stTextArea textarea { font-size: 16px !important; } /* evita zoom em iOS */
label {font-weight:600}
.section-card { border:1px solid #eee; border-radius:12px; padding:1rem 1rem .8rem 1rem; background:#fff; }
.faint { color:#6b7280; font-size:.92rem; }
hr { border:none; height:1px; background:#eee; margin: 1.2rem 0;}
/* ícone antes do label quando campo foi preenchido pela extração */
.field-aih label:before { content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%; background:#3b82f6; margin-right:.5rem; vertical-align:middle; }
.field-ocr label:before { content:""; display:inline-block; width:.6rem; height:.6rem; border-radius:50%; background:#22c55e; margin-right:.5rem; vertical-align:middle; }
</style>
"""
st.markdown(MOBILE_CSS, unsafe_allow_html=True)

# ----------------------------------------------------------
# CONSTANTES / MAPAS
# ----------------------------------------------------------
HEMOBA_TEMPLATE_PATH = "modelo_hemo.pdf"

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
    # aceita letras com acento e espaços; remove dígitos/sinais soltos
    partes = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s'.-]+", txt)
    val = " ".join(partes).strip()
    # compacta espaços
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
# PDF → TEXTO (PyMuPDF) + PARSER ROBUSTO
# ----------------------------------------------------------
def get_page_lines(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    # "text" mantém quebras de linha melhor do que "blocks" pra esse layout
    raw = page.get_text("text")
    # normaliza quebras/espacos
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]  # remove vazias
    return lines, raw

def pick_after(lines, label, max_ahead=3, prefer_digits=False, prefer_date=False):
    """Procura um rótulo (label) e retorna a melhor linha seguinte (até max_ahead)."""
    label_norm = label.lower()
    for i, ln in enumerate(lines):
        if label_norm in ln.lower():
            # tenta pegar no mesmo ln após ':'
            same = None
            if ":" in ln:
                same = ln.split(":", 1)[1].strip()
                if same and not parece_rotulo(same):
                    cand = same
                    # filtros
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

def parse_aih_from_text(lines):
    # campos base
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
        # campos manuais / padrão
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

    # EXTRAÇÕES direcionadas
    data["nome_paciente"]        = limpar_nome(pick_after(lines, "Nome do Paciente"))
    data["nome_genitora"]        = limpar_nome(pick_after(lines, "Nome da Mãe"))
    data["cartao_sus"]           = so_digitos(pick_after(lines, "CNS", prefer_digits=True))
    data["data_nascimento"]      = normaliza_data(pick_after(lines, "Data de Nasc", prefer_date=True))
    # sexo às vezes aparece no mesmo bloco (Feminino/Masculino)
    sx = pick_after(lines, "Sexo")
    if "fem" in sx.lower(): data["sexo"] = "Feminino"
    elif "mas" in sx.lower(): data["sexo"] = "Masculino"
    # raca/cor
    rc = pick_after(lines, "Raça")
    if not rc:
        rc = pick_after(lines, "Raça/Cor")
    data["raca"] = limpar_nome(rc).upper() if rc else ""

    # telefone paciente
    tel = pick_after(lines, "Telefone", prefer_digits=True)
    data["telefone_paciente"] = re.sub(r"(\d{2})(\d{4,5})(\d{4})", r"(\1) \2-\3", so_digitos(tel)) if tel else ""

    data["prontuario"]           = so_digitos(pick_after(lines, "Prontuário", prefer_digits=True))
    data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referência"))
    if not data["municipio_referencia"]:
        data["municipio_referencia"] = limpar_nome(pick_after(lines, "Município de Referencia"))
    data["uf"]                   = (pick_after(lines, "UF") or "").strip()[:2].upper()

    # CEP pode vir colado com outra coisa
    cep = so_digitos(pick_after(lines, "CEP", prefer_digits=True))
    data["cep"] = cep[:8] if len(cep) >= 8 else ""

    # Endereço: pega a linha depois de "Endereço Residencial" ou "Endereço completo"
    end = pick_after(lines, "Endereço Residencial")
    if not end:
        end = pick_after(lines, "Endereço completo")
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

# ----------------------------------------------------------
# OCR (opcional, leve e protegido por try/except)
#  - Se rapidocr-onnxruntime estiver instalado, usa.
#  - Senão, não quebra o app (só informa).
# ----------------------------------------------------------
def try_rapid_ocr(image_bytes: bytes):
    try:
        from rapidocr_onnxruntime import RapidOCR
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        arr = np.array(img)
        ocr = RapidOCR()  # modelos já vêm com o pacote (sem download em runtime)
        result, _ = ocr(arr)
        # junta as strings na ordem de leitura
        txt = "\n".join([r[1] for r in result]) if result else ""
        lines = [re.sub(r"\s+", " ", ln.strip()) for ln in txt.splitlines()]
        lines = [ln for ln in lines if ln]
        parsed = parse_aih_from_text(lines)
        return parsed, txt
    except Exception as e:
        st.info(f"OCR opcional não disponível: {e}")
        return {}, ""

def extract_from_image(file):
    # tentativa de OCR leve (não obrigatório)
    return try_rapid_ocr(file.read())

# ----------------------------------------------------------
# FORM: helpers
# ----------------------------------------------------------
def show_badge(origem: str):
    if origem == "AIH":
        st.markdown('<span class="badge"><span class="dot dot-aih"></span>AIH</span>', unsafe_allow_html=True)
    elif origem == "OCR":
        st.markdown('<span class="badge"><span class="dot dot-ocr"></span>OCR</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="badge"><span class="dot dot-man"></span>Manual</span>', unsafe_allow_html=True)

def update_phone_when_hospital_changes():
    hosp = st.session_state.get("hospital", "")
    if hosp in HOSPITAIS:
        st.session_state["telefone_unidade"] = HOSPITAIS[hosp]

# ----------------------------------------------------------
# ESTADO INICIAL
# ----------------------------------------------------------
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
    # origem de cada campo: 'MAN', 'AIH', 'OCR'
    st.session_state.origem = {k: "MAN" for k in st.session_state.dados.keys()}

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("🩸 Gerador Automático de Ficha HEMOBA")
st.caption("Envie **PDF da AIH** (preferencial) **ou foto** (JPG/PNG). Campos marcados mostram a origem: "
           '<span class="badge"><span class="dot dot-aih"></span>AIH</span> '
           '<span class="badge"><span class="dot dot-ocr"></span>OCR</span> '
           '<span class="badge"><span class="dot dot-man"></span>Manual</span>.', unsafe_allow_html=True)

with st.container():
    st.subheader("1) Enviar Ficha AIH (PDF) ou Foto")
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
            # aplica ao estado e marca origem por campo
            for k, v in dados.items():
                if v:
                    st.session_state.dados[k] = v
                    st.session_state.origem[k] = origem
            st.success("Dados extraídos! Revise e complete abaixo.")
        else:
            st.warning("Não foi possível extrair automaticamente. Você pode preencher manualmente abaixo.")

st.subheader("2) Revisar e completar formulário ↩︎")

# CARD: Identificação
with st.container():
    st.markdown("### Identificação do Paciente")

    def label_with_origin(lbl, key):
        cls = "field-aih" if st.session_state.origem.get(key) == "AIH" else \
              "field-ocr" if st.session_state.origem.get(key) == "OCR" else ""
        st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
        st.text_input(lbl, key=key, value=st.session_state.dados.get(key, ""))
        st.markdown('</div>', unsafe_allow_html=True)

    label_with_origin("Nome do Paciente", "nome_paciente")
    label_with_origin("Nome da Mãe", "nome_genitora")
    label_with_origin("Cartão SUS (CNS)", "cartao_sus")
    label_with_origin("Data de Nascimento (DD/MM/AAAA)", "data_nascimento")

    # Sexo / Raça
    st.markdown(f'<div class="{"field-aih" if st.session_state.origem.get("sexo")=="AIH" else ("field-ocr" if st.session_state.origem.get("sexo")=="OCR" else "")}">', unsafe_allow_html=True)
    st.radio("Sexo", ["Feminino", "Masculino"], key="sexo", index=0 if st.session_state.dados.get("sexo","Feminino")=="Feminino" else 1)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(f'<div class="{"field-aih" if st.session_state.origem.get("raca")=="AIH" else ("field-ocr" if st.session_state.origem.get("raca")=="OCR" else "")}">', unsafe_allow_html=True)
    st.radio("Raça/Cor", ["BRANCA", "PRETA", "PARDA", "AMARELA", "INDÍGENA"],
             key="raca",
             index=(["BRANCA","PRETA","PARDA","AMARELA","INDÍGENA"].index(st.session_state.dados.get("raca","PARDA")) if st.session_state.dados.get("raca") in ["BRANCA","PRETA","PARDA","AMARELA","INDÍGENA"] else 2))
    st.markdown('</div>', unsafe_allow_html=True)

    label_with_origin("Telefone do Paciente", "telefone_paciente")
    label_with_origin("Núm. Prontuário", "prontuario")

# CARD: Endereço
with st.container():
    st.markdown("### Endereço")
    label_with_origin("Endereço completo", "endereco_completo")
    label_with_origin("Município de referência", "municipio_referencia")
    label_with_origin("UF", "uf")
    label_with_origin("CEP", "cep")

# CARD: Estabelecimento
with st.container():
    st.markdown("### Estabelecimento (selecione)")
    st.radio("🏥 Hospital / Unidade de saúde", list(HOSPITAIS.keys()), key="hospital",
             on_change=update_phone_when_hospital_changes)
    st.text_input("☎️ Telefone da Unidade (padrão, pode ajustar)", key="telefone_unidade")

# CARD: Data & Hora
with st.container():
    st.markdown("### Data e Hora")
    st.date_input("📅 Data", key="data", value=st.session_state.dados.get("data", date.today()))
    st.time_input("⏰ Hora", key="hora", value=st.session_state.dados.get("hora", datetime.now().time().replace(microsecond=0)))

# CARD: Dados clínicos
with st.container():
    st.markdown("### Dados clínicos (manuais)")
    st.text_input("🩺 Diagnóstico", key="diagnostico")
    st.text_input("⚖️ Peso (kg)", key="peso")
    st.radio("🩸 Antecedente Transfusional?", ["Não", "Sim"], key="antecedente_transfusional")
    st.radio("🤰 Antecedentes Obstétricos?", ["Não", "Sim"], key="antecedentes_obstetricos")
    st.radio("✍️ Modalidade de Transfusão", ["Rotina", "Programada", "Urgência", "Emergência"], key="modalidade_transfusao")

# BOTÃO FINAL (placeholder: aqui você chama seu gerador de PDF do template)
st.button("Gerar PDF Final", type="primary")

# ----------------------------------------------------------
# DEBUG / LOGS
# ----------------------------------------------------------
with st.expander("🐿️ Ver texto extraído e pares (debug)"):
    # mostra último raw_text se houver (pdf ou ocr)
    st.text_area("Texto bruto", value="", height=220)
    # snapshot dos dados atuais
    snap = st.session_state.dados.copy()
    # converter datatypes pra json-friendly
    if isinstance(snap.get("data"), date):
        snap["data"] = snap["data"]
    if isinstance(snap.get("hora"), time):
        snap["hora"] = snap["hora"]
    st.json(snap)

    # downloads (json/csv)
    import json, csv
    buf_json = io.BytesIO(json.dumps(snap, ensure_ascii=False, indent=2).encode("utf-8"))
    st.download_button("Baixar .jsonl", data=buf_json, file_name="extracao.jsonl", mime="application/json")

    # csv 1 linha
    buf_csv = io.StringIO()
    writer = csv.DictWriter(buf_csv, fieldnames=list(snap.keys()))
    writer.writeheader()
    writer.writerow(snap)
    st.download_button("Baixar .csv", data=buf_csv.getvalue().encode("utf-8"), file_name="extracao.csv", mime="text/csv")