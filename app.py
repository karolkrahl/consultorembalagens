# app.py
import os
import json
import re
import streamlit as st
from openai import OpenAI

# ============== CONFIG ==============
st.set_page_config(page_title="Consultor de Embalagens • SuperFrete", page_icon="📦", layout="centered")

# Read API key from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ============== HELPERS ==============
def parse_dimensions(dim_str: str):
    """
    Parse dim string like '20x15x10' (cm) -> (20.0, 15.0, 10.0)
    Accepts punctuation variants and spaces.
    """
    if not dim_str:
        return None
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)', dim_str)
    if not m:
        return None
    c, l, a = m.groups()
    def to_float(s): 
        return float(s.replace(",", "."))
    return (to_float(c), to_float(l), to_float(a))

def cubagem_kg(c, l, a, fator=6000.0):
    """
    Peso cubado (kg) aproximado = (C*L*A)/fator, com C,L,A em cm.
    Fator padrão 6000 (pode variar por transportadora).
    """
    return (c * l * a) / fator

def call_consultor_ia(payload: dict, model: str = "gpt-4o-mini"):
    """
    Calls OpenAI Chat Completions API requesting strict JSON.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não encontrado. Defina a variável de ambiente no host.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = (
        "Você é o Consultor de Embalagens da SuperFrete. "
        "Responda SEMPRE em PT-BR, didático e direto. "
        "Sua resposta DEVE ser APENAS um JSON válido seguindo exatamente o schema fornecido. "
        "Não invente políticas específicas de transportadoras. Prefira custo baixo, segurança adequada e redução de cubagem. "
        "Quando houver trade-offs, explique resumidamente."
    )

    user_prompt = f"""
Dados do lojista:
- Categoria: {payload.get('categoria')}
- Produto: {payload.get('produto')}
- Fragilidade: {payload.get('fragilidade')}
- Dimensões (cm): {payload.get('dimensoes_cm')} (formato CxLxA)
- Peso (kg): {payload.get('peso_kg')}
- Quantidade por envio: {payload.get('qtd_por_envio')}
- Destino predominante: {payload.get('destino')}
- Budget embalagem por pedido (R$): {payload.get('budget')}
- Dores: {', '.join(payload.get('dores', [])) if payload.get('dores') else ''}

Tarefa: gere recomendações de embalagem.

Schema de saída (JSON estrito):
{{
  "caixa_recomendada": {{"descricao": "string", "dimensoes_cm": "CxLxA", "justificativa": "string"}},
  "protecao_interna": [{{"tipo": "string", "qtde_sugerida": "string", "observacao": "string"}}],
  "lacres_e_reforcos": [{{"tipo": "string", "observacao": "string"}}],
  "estimativa_custos": {{"embalagem_total_r$": "number", "observacoes": "string"}},
  "riscos_e_mitigacoes": [{{"risco": "string", "mitigacao": "string"}}],
  "impacto_cubagem": {{"comentario": "string"}},
  "boas_praticas": ["string", "string", "string"],
  "resumo_curto": "string"
}}

Restrições:
- Retorne APENAS o JSON do schema acima.
- Se faltarem dados, assuma o conservador e indique no campo "observacoes".
"""

    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = resp.choices[0].message.content
    return json.loads(content)

# ============== UI ==============
st.markdown("## 📦 Consultor de Embalagens (MVP)")
st.markdown("Obtenha recomendações instantâneas de embalagem com IA para reduzir avarias e cubagem.")

with st.form("form"):
    col1, col2 = st.columns(2)
    with col1:
        categoria = st.selectbox(
            "Categoria do produto",
            ["Moda", "Papelaria", "Eletrônicos leves", "Cosméticos", "Artesanato", "Outros"],
            index=1
        )
        produto = st.text_input("Descreva o produto (ex.: caneca de porcelana, caderno A5, camiseta)")
        fragilidade = st.selectbox("Fragilidade", ["Baixa", "Média", "Alta"], index=2)
        qtd = st.number_input("Quantidade por envio", min_value=1, max_value=50, value=1, step=1)
    with col2:
        dim = st.text_input("Dimensões do item (CxLxA em cm)", placeholder="ex.: 20x15x10")
        peso = st.number_input("Peso do item (kg)", min_value=0.0, max_value=100.0, value=0.3, step=0.01, format="%.2f")
        destino = st.selectbox("Destino predominante", ["Sudeste", "Sul", "Centro-Oeste", "Nordeste", "Norte", "Brasil todo"], index=0)
        budget = st.number_input("Budget de embalagem por pedido (R$) — opcional", min_value=0.0, max_value=100.0, value=0.0, step=0.10, format="%.2f")

    dores = st.multiselect(
        "Principais dores (opcional)",
        ["Avarias", "Extravio", "Devoluções", "Volume/cubagem", "Custo de embalagem"]
    )

    submitted = st.form_submit_button("Gerar recomendação")

if submitted:
    # Validate dimensions
    dims = parse_dimensions(dim)
    if not dims:
        st.error("Informe as dimensões no formato CxLxA, por exemplo: 20x15x10")
        st.stop()

    c, l, a = dims
    cubado = cubagem_kg(c, l, a, fator=6000.0)

    payload = {
        "categoria": categoria,
        "produto": produto or "Produto não informado",
        "fragilidade": fragilidade,
        "dimensoes_cm": f"{int(c)}x{int(l)}x{int(a)}",
        "peso_kg": round(peso, 3),
        "qtd_por_envio": int(qtd),
        "destino": destino,
        "budget": float(budget),
        "dores": dores,
        "peso_cubado_kg": round(cubado, 3),
    }

    with st.spinner("Gerando recomendação com IA..."):
        try:
            result = call_consultor_ia(payload)
        except Exception as e:
            st.exception(e)
            st.stop()

    st.success("Pronto! Veja sua recomendação abaixo.")

    # Header cards
    st.markdown("### ✅ Resumo")
    st.write(result.get("resumo_curto", ""))

    # Details in expanders
    colA, colB = st.columns(2)
    with colA:
        st.subheader("📦 Caixa recomendada")
        caixa = result.get("caixa_recomendada", {})
        st.write(f"**Tipo:** {caixa.get('descricao', '-')}\n\n**Dimensões sugeridas:** {caixa.get('dimensoes_cm', '-')}")
        st.caption(caixa.get("justificativa", ""))

        st.subheader("🧱 Proteção interna")
        for item in result.get("protecao_interna", []):
            st.write(f"- **{item.get('tipo','')}** — {item.get('qtde_sugerida','')}")
            st.caption(item.get("observacao",""))

    with colB:
        st.subheader("🔒 Lacres & reforços")
        for lacre in result.get("lacres_e_reforcos", []):
            st.write(f"- **{lacre.get('tipo','')}** — {lacre.get('observacao','')}")

        st.subheader("💰 Estimativa de custos")
        custos = result.get("estimativa_custos", {})
        if "embalagem_total_r$" in custos:
            st.metric("Custo estimado de embalagem", f"R${custos['embalagem_total_r$']:.2f}")
        st.caption(custos.get("observacoes", ""))

    st.subheader("⚠️ Riscos & mitigação")
    for r in result.get("riscos_e_mitigacoes", []):
        st.write(f"- **{r.get('risco','')}:** {r.get('mitigacao','')}")

    st.subheader("📉 Impacto na cubagem")
    st.write(result.get("impacto_cubagem", {}).get("comentario", ""))

    st.subheader("🧪 Boas práticas")
    for bp in result.get("boas_praticas", []):
        st.write(f"- {bp}")

    st.divider()
    st.caption(f"Peso real informado: **{peso:.3f} kg** | Peso cubado (fator 6000): **{payload['peso_cubado_kg']:.3f} kg** — Use o maior para cálculo tarifário.")
    st.caption("Aviso: recomendações são estimativas educativas; valide com seu fornecedor de embalagens e política de envio.")

    # Raw JSON (advanced users)
    with st.expander("Ver resposta técnica (JSON)"):
        st.json(result, expanded=False)

else:
    st.info("Preencha os campos e clique em **Gerar recomendação** para ver sua consultoria de embalagem.")
    st.caption("Dica: use medidas em cm e pense no menor volume que ainda proteja o produto para reduzir cubagem.")
