# Consultor de Embalagens — SuperFrete (MVP Streamlit)

Este é um MVP de **assistente consultivo** que recomenda embalagem, proteção e boas práticas com IA.

## Como rodar localmente
1. Python 3.10+
2. `pip install -r requirements.txt`
3. Defina a variável de ambiente `OPENAI_API_KEY`
   - Linux/Mac: `export OPENAI_API_KEY=...`
   - Windows (PowerShell): `$Env:OPENAI_API_KEY="..."`
4. `streamlit run app.py`

## Deploy rápido (Render.com)
- Novo Web Service → Python
- Build command: `pip install -r requirements.txt`
- Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
- Environment: adicionar `OPENAI_API_KEY`

## Embed no HubSpot
Use um módulo HTML com:
```html
<iframe src="https://SEU-DOMINIO-DO-APP" width="100%" height="880" style="border:0;border-radius:12px;overflow:hidden" loading="lazy"></iframe>
```
