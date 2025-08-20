# app.py
import os
import json
import re
import time
import requests
import streamlit as st
from openai import OpenAI
from json import JSONDecodeError

# ============== CONFIG ==============
st.set_page_config(page_title="Consultor de Embalagens • SuperFrete", page_icon="📦", layout="centered")

# ============== CSS CUSTOM ==============
st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&display=swap');
        html, body, [class*="css"]  { font-family: 'Poppins', sans-serif; }
        .stButton>button {
            background-color: #0fae79; color: #fff; border: none;
            border-radius: 8px; padding: 0.6em 1.2em; font-weight: 600;
        }
        .stButton>button:hover { background-color: #0c8c62; color: #fff; }
        h1, h2, h3, h4, h5, h6 { color: #0fae79; font-weight: 700; }
        a { color: #0fae79; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
""", unsafe_allow_html=True)

# Read API keys from environment
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPERFRETE_API_TOKEN = os.getenv("SUPERFRETE_API_TOKEN")  # defina no Render

# ============== HELPERS ==============
def parse_dimensions(dim_str: str):
    """Parse '20x15x10' (cm) -> (20.0, 15.0, 10.0)."""
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
    """Peso cubado (kg) ≈ (C*L*A)/fator, com C,L,A em cm."""
    return (c * l * a) / fator

def sanitize_cep(cep: str):
    """Mantém apenas dígitos; retorna com 8 dígitos ou None."""
    if not cep:
        return None
    digits = re.sub(r"\D", "", cep)
    return digits if len(digits) == 8 else None

def estimate_packaging_cost(c, l, a, fragilidade: str):
    """
    Heurística simples para estimar custo de embalagem (R$) pela volumetria.
    Pequenas caixas ~ R$1,50–3,50; ajusta por fragilidade.
    """
    volume = c * l * a  # cm³
    base = 0.8 + (volume * 0.0004)  # 18x23x4 -> ~0.8 + 1.65*0.4 ≈ R$1.46
    extra = 0.2 if fragilidade == "Baixa" else (0.4 if fragilidade == "Média" else 0.8)
    return round(base + extra, 2)

def call_consultor_ia(payload: dict, model: str = "gpt-4o-mini"):
    """Chama OpenAI pedindo JSON estrito."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY não encontrado. Defina a variável de ambiente.")

    client = OpenAI(api_key=OPENAI_API_KEY)

    system_prompt = (
        "Você é o Consultor de Embalagens da SuperFrete. "
        "Responda SEMPRE em PT-BR, didático e direto. "
        "Sua resposta DEVE ser APENAS um JSON válido seguindo exatamente o schema fornecido. "
        "Não invente políticas específicas de transportadoras. Prefira custo baixo, segurança adequada e redução de cubagem. "
        "Quando houver trade-offs, explique resumidamente. "
        "Considere preços médios de mercado no Brasil; estime o custo da embalagem mesmo sem orçamento informado."
    )

    # (Removemos 'budget' do prompt — custo deve ser estimado sempre)
    user_prompt = f"""
Dados do lojista:
- Categoria: {payload.get('categoria')}
- Produto: {payload.get('produto')}
- Fragilidade: {payload.get('fragilidade')}
- Dimensões (cm): {payload.get('dimensoes_cm')} (formato CxLxA)
- Peso (kg): {payload.get('peso_kg')}
- Quantidade por envio: {payload.get('qtd_por_envio')}
- Destino predominante: {payload.get('destino')}
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

def with_retry(func, retries=1, delay=2):
    """Retry simples p/ rate-limit transitório."""
    for i in range(retries + 1):
        try:
            return func()
        except Exception as e:
            if "rate limit" in str(e).lower() and i < retries:
                time.sleep(delay * (2 ** i))
                continue
            raise

def call_superfrete_quote(token, cep_from, cep_to, length_cm, width_cm, height_cm, weight_kg):
    """
    Chama a API de cotação de frete da SuperFrete.
    Usa as DIMENSÕES DA EMBALAGEM sugerida e peso final (real vs. cubado).
    """
    if not token:
        return {"error": "TOKEN ausente"}

    url = "https://api.superfrete.com.br/v0/quote"  # ajuste se o endpoint for diferente
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    body = {
        "from": {"postal_code": cep_from},
        "to": {"postal_code": cep_to},
        "package": {
            "length": int(round(length_cm)),
            "width": int(round(width_cm)),
            "height": int(round(height_cm)),
            "weight": round(float(weight_kg), 3)
        }
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)
        if r.status_code == 401:
            return {"error": "Token inválido/expirado (401). Revogue e crie um novo token e configure SUPERFRETE_API_TOKEN."}
        if r.status_code >= 400:
            return {"error": f"Erro {r.status_code}: {r.text}"}
        data = r.json()
        ofertas = data.get("data") or data  # depende do formato da API
        if not ofertas or not isinstance(ofertas, list):
            return {"error": "Resposta inesperada da API de frete."}

        # melhor preço e melhor prazo
        best_price = min(ofertas, key=lambda x: float(x.get("price", 9e9)))
        best_time = min(ofertas, key=lambda x: int((x.get("delivery_time") or {}).get("days", 9e9)))
        return {"best_price": best_price, "best_time": best_time, "raw": ofertas}

    except requests.RequestException as e:
        return {"error": f"Falha de rede: {e}"}

# ============== UI ==============
st.markdown("## 📦 Consultor de Embalagens (MVP)")
st.markdown("Obtenha recomendações instantâneas de embalagem com IA e **simule o frete** com base nas dimensões **recomendadas**.")

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
        cep_from = st.text_input("CEP de origem", placeholder="ex.: 01001-000")
    with col2:
        dim = st.text_input("Dimensões do item (CxLxA em cm)", placeholder="ex.: 20x15x10")
        peso = st.number_input("Peso do item (kg)", min_value=0.0, max_value=100.0, value=0.3, step=0.01, format="%.2f")
        destino = st.selectbox("Destino predominante", ["Sudeste", "Sul", "Centro-Oeste", "Nordeste", "Norte", "Brasil todo"], index=0)
        cep_to = st.text_input("CEP de destino (opcional)", placeholder="ex.: 20040-000")

    dores = st.multiselect(
        "Principais dores (opcional)",
        ["Avarias", "Extravio", "Devoluções", "Volume/cubagem", "Custo de embalagem"]
    )

    submitted = st.form_submit_button("Gerar recomendação")

if submitted:
    # Validar dimensões do item
    dims_item = parse_dimensions(dim)
    if not dims_item:
        st.error("Informe as dimensões no formato CxLxA, por exemplo: 20x15x10")
        st.stop()

    c, l, a = dims_item
    cubado_item = cubagem_kg(c, l, a, fator=6000.0)

    payload = {
        "categoria": categoria,
        "produto": produto or "Produto não informado",
        "fragilidade": fragilidade,
        "dimensoes_cm": f"{int(c)}x{int(l)}x{int(a)}",
        "peso_kg": round(peso, 3),
        "qtd_por_envio": int(qtd),
        "destino": destino,
        "dores": dores,
        "peso_cubado_kg": round(cubado_item, 3),
    }

    with st.spinner("Gerando recomendação com IA..."):
        try:
            result = with_retry(lambda: call_consultor_ia(payload))
        except Exception as e:
            emsg = str(e).lower()
            if "insufficient_quota" in emsg or ("429" in emsg and "quota" in emsg):
                st.error("Estamos sem créditos de API no momento. Verifique Billing/Usage na OpenAI e a variável OPENAI_API_KEY no Render.")
            elif "rate limit" in emsg or "429" in emsg:
                st.warning("Muitos pedidos ao mesmo tempo. Aguarde alguns segundos e tente novamente.")
            elif isinstance(e, JSONDecodeError) or "invalid json" in emsg:
                st.error("A IA retornou um formato inesperado. Tente novamente.")
            else:
                st.error("Não consegui concluir sua recomendação agora. Tente novamente.")
            st.stop()

    st.success("Pronto! Veja sua recomendação abaixo.")

    # Resumo
    st.markdown("### ✅ Resumo")
    st.write(result.get("resumo_curto", ""))

    # Caixa recomendada
    colA, colB = st.columns(2)
    with colA:
        st.subheader("📦 Caixa/embalagem recomendada")
        caixa = result.get("caixa_recomendada", {}) or {}
        st.write(f"**Tipo:** {caixa.get('descricao', '-')}")
        st.write(f"**Dimensões sugeridas:** {caixa.get('dimensoes_cm', '-')}")
        st.caption(caixa.get("justificativa", ""))

        st.subheader("🧱 Proteção interna")
        for item in result.get("protecao_interna", []):
            st.write(f"- **{item.get('tipo','')}** — {item.get('qtde_sugerida','')}")
            st.caption(item.get("observacao",""))

    with colB:
        st.subheader("🔒 Lacres & reforços")
        for lacre in result.get("lacres_e_reforcos", []):
            st.write(f"- **{lacre.get('tipo','')}** — {lacre.get('observacao','')}")

        st.subheader("💰 Estimativa de custo da embalagem")
        custos = result.get("estimativa_custos", {}) or {}
        custo_ai = custos.get("embalagem_total_r$")
        # Fallback heurístico se a IA não trouxer valor
        dims_caixa = parse_dimensions(caixa.get("dimensoes_cm","")) or (c, l, a)
        custo_est = estimate_packaging_cost(*dims_caixa, fragilidade=fragilidade) if custo_ai is None else float(custo_ai)
        st.metric("Custo estimado da embalagem", f"R${custo_est:.2f}")
        st.caption(custos.get("observacoes", "Estimativa baseada em preço médio de mercado."))

    # Riscos, cubagem, boas práticas
    st.subheader("⚠️ Riscos & mitigação")
    for r in result.get("riscos_e_mitigacoes", []):
        st.write(f"- **{r.get('risco','')}:** {r.get('mitigacao','')}")

    st.subheader("📉 Impacto na cubagem")
    st.write(result.get("impacto_cubagem", {}).get("comentario", ""))

    st.subheader("🧪 Boas práticas")
    for bp in result.get("boas_praticas", []):
        st.write(f"- {bp}")

    # ===== Frete (SuperFrete) =====
    st.divider()
    st.markdown("### 🚚 Estimativa de frete (SuperFrete)")

    cep_from_s = sanitize_cep(cep_from)
    cep_to_s   = sanitize_cep(cep_to)

    if not cep_from_s:
        st.info("Informe um **CEP de origem** válido para simular o frete.")
    elif not cep_to_s:
        st.info("Informe um **CEP de destino** para ver o preço e prazo.")
    elif not SUPERFRETE_API_TOKEN:
        st.warning("Token da SuperFrete não configurado no servidor (SUPERFRETE_API_TOKEN).")
    else:
        # Usar dimensões da EMBALAGEM recomendada (se existir), senão as do item
        Cc, Ll, Aa = dims_caixa  # cm
        # Peso final: considerar embalagem (≈+50g) e peso cubado da EMBALAGEM
        peso_extra = 0.05
        peso_real_final = float(peso) + peso_extra
        cubado_caixa = cubagem_kg(Cc, Ll, Aa, fator=6000.0)
        peso_para_cotacao = max(peso_real_final, cubado_caixa)

        with st.spinner("Consultando fretes na SuperFrete..."):
            cot = call_superfrete_quote(
                token=SUPERFRETE_API_TOKEN,
                cep_from=cep_from_s,
                cep_to=cep_to_s,
                length_cm=Cc, width_cm=Ll, height_cm=Aa,
                weight_kg=peso_para_cotacao
            )

        if cot.get("error"):
            st.error(cot["error"])
        else:
            bp = cot["best_price"]; bt = cot["best_time"]
            colp, colt = st.columns(2)
            with colp:
                st.subheader("💵 Melhor preço")
                st.write(f"**Transportadora/serviço:** {bp.get('company',{}).get('name','-')} {bp.get('service','')}")
                st.write(f"**Preço:** R${float(bp.get('price',0)):.2f}")
                dias = (bp.get('delivery_time') or {}).get('days')
                if dias: st.write(f"**Prazo:** {dias} dia(s)")

            with colt:
                st.subheader("⏱️ Melhor prazo")
                st.write(f"**Transportadora/serviço:** {bt.get('company',{}).get('name','-')} {bt.get('service','')}")
                st.write(f"**Preço:** R${float(bt.get('price',0)):.2f}")
                dias = (bt.get('delivery_time') or {}).get('days')
                if dias: st.write(f"**Prazo:** {dias} dia(s)")

    st.divider()
    st.caption(f"Peso real informado: **{peso:.3f} kg** | Peso cubado do item (fator 6000): **{cubado_item:.3f} kg**.")
    st.caption("Aviso: recomendações e estimativas são educativas; valide com seu fornecedor e política de envio.")

    # CTA final
    st.link_button("Emitir seu frete com a SuperFrete", "https://web.superfrete.com/#/calcular-correios")

    # Raw JSON (técnico)
    with st.expander("Ver resposta técnica (JSON)"):
        st.json(result, expanded=False)

else:
    st.info("Preencha os campos e clique em **Gerar recomendação** para ver sua consultoria + simulação de frete.")
    st.caption("Use medidas em cm e pense na menor volumetria que ainda proteja o produto (reduz cubagem).")
