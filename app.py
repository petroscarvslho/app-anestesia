# app.py — Parser AIH por colunas (PyMuPDF) + UI Streamlit opcional
# -----------------------------------------------------------------------------
# Uso:
#   CLI......: python3 app.py /caminho/arquivo.pdf
#   Streamlit: streamlit run app.py
# -----------------------------------------------------------------------------

import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Dict
from dataclasses import dataclass

import fitz  # PyMuPDF
from unidecode import unidecode

# =========================
# Helpers de normalização
# =========================

def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def up_noacc(s: str) -> str:
    return norm_space(unidecode(s or "").upper())

def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def normalize_cep(s: str) -> str:
    d = only_digits(s)
    if len(d) >= 8:
        d = d[:8]
        return f"{d[:5]}-{d[5:]}"
    m = re.search(r"\b(\d{5})[- ]?(\d{3})\b", s or "")
    return f"{m.group(1)}-{m.group(2)}" if m else norm_space(s)

def normalize_uf(s: str) -> str:
    t = up_noacc(s)
    m = re.search(r"\b([A-Z]{2})\b", t)
    return m.group(1) if m else ""

def normalize_sexo(s: str) -> str:
    t = up_noacc(s)
    if "MASC" in t: return "MASCULINO"
    if "FEM" in t:  return "FEMININO"
    return norm_space(s)

def normalize_raca(s: str) -> str:
    t = up_noacc(s)
    for k in ["BRANCA", "PARDA", "PRETA", "AMARELA", "INDIGENA", "INDÍGENA"]:
        if k in t: return "INDIGENA" if "Í" in k or "I" else k
    return norm_space(s)

def normalize_cns(s: str) -> str:
    d = only_digits(s)
    if len(d) >= 15:
        return d[:15]
    return d or norm_space(s)

def normalize_date(text: str) -> str:
    text = text or ""
    m = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{2,4})\b", text)
    if m:
        d, mm, y = m.groups()
        y = y if len(y) == 4 else ("20"+y if int(y) < 50 else "19"+y)
        return f"{d}/{mm}/{y}"
    digs = only_digits(text)
    for i in range(len(digs)-7):
        d, mm, y = digs[i:i+2], digs[i+2:i+4], digs[i+4:i+8]
        try:
            datetime(int(y), int(mm), int(d))
            return f"{d}/{mm}/{y}"
        except Exception:
            pass
    return norm_space(text)

# =========================
# Estruturas de dados
# =========================

@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

Line = List[Word]

# =========================
# Leitura e agrupamento
# =========================

def page_words(page) -> List[Word]:
    """Obtém palavras com coords (compatível PyMuPDF 1.26.x)."""
    out = []
    for w in page.get_text("words"):
        x0, y0, x1, y1, txt = w[:5]
        if txt and txt.strip():
            out.append(Word(float(x0), float(y0), float(x1), float(y1), txt))
    return out

def group_lines(words: List[Word], y_tol: float = 2.8) -> List[Line]:
    """Agrupa por Y aproximado com tolerância."""
    words = sorted(words, key=lambda w: (w.y0, w.x0))
    lines: List[Line] = []
    for w in words:
        if not lines:
            lines.append([w]); continue
        last_y = sum(x.y0 for x in lines[-1]) / len(lines[-1])
        if abs(w.y0 - last_y) <= y_tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w.x0)
    return lines

def line_text(line: Line) -> str:
    return norm_space(" ".join(w.text for w in line))

# =========================
# Cabeçalhos → colunas (janelas X)
# =========================

def find_header_line(lines: List[Line], required_keywords: List[str]) -> int:
    """Retorna índice da melhor linha-cabeçalho contendo a maioria dos tokens."""
    req = [up_noacc(k) for k in required_keywords]
    best_idx, best_hits = -1, -1
    for i, ln in enumerate(lines):
        txt = up_noacc(line_text(ln))
        hits = sum(1 for k in req if k in txt)
        if hits > best_hits:
            best_hits, best_idx = hits, i
    return best_idx if best_hits >= max(1, len(required_keywords)//2) else -1

def build_columns_from_header(line: Line, label_map: Dict[str, List[str]]) -> List[Tuple[float, float, str]]:
    """Cria faixas (x0,x1,label) a partir das palavras do cabeçalho."""
    anchors = []
    for col_name, tokens in label_map.items():
        min_x = None
        for w in line:
            uw = up_noacc(w.text)
            if any(t in uw for t in tokens):
                min_x = w.x0 if min_x is None else min(min_x, w.x0)
        if min_x is not None:
            anchors.append((min_x, col_name))
    anchors.sort(key=lambda t: t[0])
    cols = []
    for idx, (x, name) in enumerate(anchors):
        left  = (anchors[idx-1][0] + x)/2 if idx > 0 else x - 5
        right = (x + anchors[idx+1][0])/2 if idx < len(anchors)-1 else x + 1200
        cols.append((left, right, name))
    return cols

def assign_values_to_columns(value_line: Line, cols: List[Tuple[float,float,str]]) -> Dict[str, str]:
    out = {name: "" for *_ , name in cols}
    for w in value_line:
        cx = (w.x0 + w.x1)/2
        for left, right, name in cols:
            if left <= cx <= right:
                out[name] = norm_space(out[name] + " " + w.text)
                break
    return out

# =========================
# Parsers dos blocos
# =========================

def parse_bloco_nome_atend_pront(lines: List[Line]) -> Dict[str, str]:
    # Ex.: "Nome do Paciente  Atendimento Núm.  Prontuário"
    idx = find_header_line(lines, ["NOME", "PACIENTE", "ATEND", "PRONT"])
    if idx < 0 or idx+1 >= len(lines):
        return {}
    header = lines[idx]
    values = lines[idx+1]
    label_map = {
        "nome_paciente": ["NOME", "PACIENTE"],
        "atendimento_num": ["ATEND"],
        "prontuario": ["PRONT"],
    }
    cols = build_columns_from_header(header, label_map)
    raw = assign_values_to_columns(values, cols)
    return {
        "nome_paciente": up_noacc(raw.get("nome_paciente", "")),
        "atendimento_num": norm_space(raw.get("atendimento_num", "")),
        "prontuario": norm_space(only_digits(raw.get("prontuario", "")) or raw.get("prontuario", "")),
    }

def parse_bloco_nome_mae_tel(lines: List[Line]) -> Dict[str, str]:
    # Ex.: "Nome da Mãe   Nome do Responsável   Telefone Celular"
    idx = find_header_line(lines, ["NOME", "MAE", "MÃE", "TELEFONE"])
    if idx < 0 or idx+1 >= len(lines):
        return {}
    header = lines[idx]
    values = lines[idx+1]
    label_map = {
        "nome_genitora": ["MAE", "MÃE"],
        "responsavel": ["RESPONS"],
        "telefone": ["TELEFONE", "CELULAR", "CONTATO"],
    }
    cols = build_columns_from_header(header, label_map)
    raw = assign_values_to_columns(values, cols)
    telefone = raw.get("telefone", "")
    return {
        "nome_genitora": up_noacc(raw.get("nome_genitora", "")),
        "telefone_paciente": norm_space(telefone),
    }

def parse_bloco_cns_data_sexo_raca_tel(lines: List[Line]) -> Dict[str, str]:
    # Ex.: "CNS  Data de Nasc  Sexo  Raça/cor  Telefone de Contato"
    idx = find_header_line(lines, ["CNS", "DATA", "SEXO", "RACA", "COR", "TELEFONE"])
    if idx < 0 or idx+1 >= len(lines):
        return {}
    header = lines[idx]
    values = lines[idx+1]
    label_map = {
        "cns": ["CNS"],
        "data_nasc": ["DATA", "NASC"],
        "sexo": ["SEXO"],
        "raca": ["RACA", "COR"],
        "telefone": ["TELEFONE"],
    }
    cols = build_columns_from_header(header, label_map)
    raw = assign_values_to_columns(values, cols)
    return {
        "cns": normalize_cns(raw.get("cns", "")),
        "data_nascimento": normalize_date(raw.get("data_nasc", "")),
        "sexo": normalize_sexo(raw.get("sexo", "")),
        "raca": normalize_raca(raw.get("raca", "")),
        "telefone_paciente": norm_space(raw.get("telefone", "")),
    }

def parse_bloco_endereco(lines: List[Line]) -> Dict[str, str]:
    # Ex.: linha com "Endereço" e, na linha seguinte, o texto do endereço
    idx = find_header_line(lines, ["ENDEREC"])
    if idx < 0 or idx+1 >= len(lines):
        return {}
    valores = lines[idx+1]
    return {"endereco_completo": norm_space(line_text(valores))}

def parse_bloco_cpf_mun_uf_cep_nat(lines: List[Line]) -> Dict[str, str]:
    # Ex.: "CPF  Municipio de Referência  Cód. IBGE do Município  UF  CEP  Naturalidade"
    idx = find_header_line(lines, ["CPF", "MUNIC", "UF", "CEP"])
    if idx < 0 or idx+1 >= len(lines):
        return {}
    header = lines[idx]
    values = lines[idx+1]
    label_map = {
        "cpf": ["CPF"],
        "municipio": ["MUNIC"],
        "cod_ibge": ["IBGE"],
        "uf": ["UF"],
        "cep": ["CEP"],
        "naturalidade": ["NATUR"],
    }
    cols = build_columns_from_header(header, label_map)
    raw = assign_values_to_columns(values, cols)
    return {
        "cpf": norm_space(raw.get("cpf", "")),
        "municipio": up_noacc(raw.get("municipio", "")),
        "cod_ibge_municipio": norm_space(raw.get("cod_ibge", "")),
        "uf": normalize_uf(raw.get("uf", "")),
        "cep": normalize_cep(raw.get("cep", "")),
        "naturalidade": up_noacc(raw.get("naturalidade", "")),
    }

# =========================
# Parser principal
# =========================

def parse_aih(pdf_path: str) -> Dict[str, str]:
    doc = fitz.open(pdf_path)
    page = doc[0]
    words = page_words(page)
    lines = group_lines(words, y_tol=3.0)

    result: Dict[str, str] = {}

    # Ordem dos blocos
    for fn in (
        parse_bloco_nome_atend_pront,
        parse_bloco_nome_mae_tel,
        parse_bloco_cns_data_sexo_raca_tel,
        parse_bloco_endereco,
        parse_bloco_cpf_mun_uf_cep_nat,
    ):
        try:
            data = fn(lines)
            for k, v in data.items():
                if v and not result.get(k):  # não sobrescreve se já tem valor
                    result[k] = v
        except Exception:
            pass

    # Correção sexo x raça
    if result.get("raca") in ["MASCULINO", "FEMININO"]:
        if not result.get("sexo"):
            result["sexo"] = result["raca"]
        result["raca"] = ""

    return result

# =========================
# CLI simples
# =========================

def _cli():
    import json, sys
    if len(sys.argv) == 2:
        data = parse_aih(sys.argv[1])
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    # sem argumento: tenta a pasta padrão /home/ubuntu/upload
    base = Path("/home/ubuntu/upload")
    if not base.exists():
        print("Uso: python3 app.py /caminho/AIH.pdf")
        return
    for pdf in sorted(base.glob("*.PDF")) + sorted(base.glob("*.pdf")):
        d = parse_aih(str(pdf))
        print(f"\n=== {pdf.name} ===")
        print(json.dumps(d, ensure_ascii=False, indent=2))

# =========================
# UI (Streamlit) — chamado só quando precisa
# =========================

def run_streamlit_app():
    import streamlit as st
    st.set_page_config(page_title="HEMOBA • Extração AIH por Colunas", layout="centered")
    st.title("HEMOBA • Extração AIH (PDF) por Colunas")
    up = st.file_uploader("Envie um PDF AIH", type=["pdf"])
    if up:
        tmp = Path("tmp_aih.pdf")
        tmp.write_bytes(up.read())
        with st.spinner("Processando..."):
            data = parse_aih(str(tmp))
        st.success("Extração concluída.")
        st.json(data)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        _cli()
    else:
        run_streamlit_app()
