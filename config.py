"""
config.py — Única fonte da verdade para IDs, webhooks e configs por guild.

Para adicionar um novo servidor:
  1. Adicione uma entrada em SERVIDORES com o guild_id como chave.
  2. Preencha canal_unificado e os sub-dicts de cada módulo.

Para adicionar um novo módulo:
  1. Crie modules/seu_modulo.py
  2. Adicione um sub-dict "seu_modulo" em cada entrada de SERVIDORES (se precisar de config por guild).
  3. Registre em main.py.
"""

import os
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

# ── Token ──────────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")

# ── Webhooks N8N (um por módulo que envia dados) ───────────────────────────────
N8N_WEBHOOK_TI: str       = os.getenv("N8N_WEBHOOK_URL_TI", "")
N8N_WEBHOOK_SISTEMAS: str = os.getenv("N8N_WEBHOOK_URL_SIS", "")
N8N_WEBHOOK_CONTATO: str  = os.getenv("N8N_WEBHOOK_URL_CONT", "")
# SDR não envia para N8N

# ── SDR ────────────────────────────────────────────────────────────────────────
SDR_FORM_URL: str = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSdYCQQ2ngdDIkJmMBbe5iE7xusapiPdUeDjxusvChs49i_GEg/viewform?usp=header"
)

# ── Thread ─────────────────────────────────────────────────────────────────────
THREAD_AUTO_ARCHIVE_MINUTES: int = 1440  # 24 h

# ── Cargos globais (módulo Sistemas) ──────────────────────────────────────────
CHATGURU_ROLE_ID: int        = 1474430718582067320
WHOM_ROLE_ID: int            = 1474430877827203165
CLICKUP_SUPPORT_ROLE_ID: int = 1474431104046989312
EMAIL_MLR_ROLE_ID: int       = 1482021270185967656   # @mlradvogados.com
EMAIL_GMAIL_ROLE_ID: int     = 1482021588701417635   # @gmail.com
TRESCEPLUS_ROLE_ID: int      = 1484627219518062864   # 3c+
ADMIN_EXTRA_ROLE_ID: int     = 1424847269148102656   # Cargo adicional

# ── Módulo Contato ─────────────────────────────────────────────────────────────
CONTATO_TARGET_ROLE_ID: int             = 1516088193240400054  # cargo mencionado em todo tópico
CONTATO_INACTIVITY_TIMEOUT_SECONDS: int = 86400                # 24h (use 5 para teste)
CONTATO_CLOSE_DELAY_SECONDS: int        = 40                   # teste: 40s após !contato

# Modulo TI / Equipamentos
EQUIPAMENTOS_ROLE_ID: int = 1519781852611875098  # cargo mencionado nos chamados de equipamentos

CONTATO_INACTIVITY_MESSAGE: str = (
    "Eii! Atualização sobre a sua busca: :hourglass:\n\n"
    "O sistema ainda está rodando a varredura para localizar os contatos desse cliente.\n\n"
    "**Por que isso acontece?** Alguns clientes possuem cadastros muito desatualizados ou difíceis de cruzar nas bases públicas. "
    "Para não te entregar um número errado e fazer você perder tempo ligando para terceiros, nossos algoritmos estão aprofundando "
    "a busca em fontes alternativas.\n\n"
    "**O que fazer agora?** Pode focar nas suas outras demandas, não precisa se preocupar ou abrir um novo chamado. "
    "O robô continua trabalhando nesse caso em segundo plano. Assim que batermos o contato quente dele, te avisamos aqui na hora! "
    ":scales: :rocket:"
)

CONTATO_CONCLUSAO_MESSAGE: str = (
    "Opa! Busca concluída com sucesso. :white_check_mark:\n\n"
    "Fizemos uma varredura completa e conseguimos extrair **TODOS** os números de telefone possíveis desse cliente. "
    "O dossiê de contatos tá na sua mão!\n\n"
    "Agora é com você: bora iniciar as tentativas, cobrar os documentos e dar andamento nesse processo para ganharmos logo esse caso. :scales:\n\n"
    "Para facilitar a sua vida, lembre que todos os números resgatados ficam salvos e disponíveis 24h por dia neste link: :point_down:\n\n"
    "https://app.clickup.com/9011605202/v/li/901112971241\n\n"
    "Bom trabalho e vamos pra cima! :rocket:"
)

# ── Configurações por guild ────────────────────────────────────────────────────
SERVIDORES: dict[int, dict] = {

    1516880237743439913: {
        "nome": "MLR",
        "empresa_clickup": "mlr_advogados", # Usado para preencher o campo de empresa no payload enviado ao N8N/ClickUp.
        "canal_chamados_pendentes": 1517618923774939256, # Canal que recebe os cards de chamados inativos.
        "canal_unificado": 1516880239022706841,
        "canal_logs": 1516880801755693106,
        "modulos_ativos": ["sistemas", "ti", "contato", "reembolso"],

        "ti": {
            "cargo_ti":   1522244271812509696,
            "cargo_equipamentos": 1522244271812509696,
            "canal_logs": 1516880801755693106,
        },

        "sistemas": {
            "cargo_ti": 1522244271812509696,
            "chatguru_role_id": 1537886992451375216,
            "whom_role_id": 1537887544660983980,
            "clickup_support_role_id": 1537887742493720676,
            "tresceplus_role_id": 1537892081572511904,
        },

        "contato": {
            "target_role_id": 1537892262363795477,
            "keeper_role_id": 1537892262363795477,
        },
    },

    1409995330795081738: {
        "nome": "FUPER",
        "empresa_clickup": "fuper", # Usado para preencher o campo de empresa no payload enviado ao N8N/ClickUp.
        "canal_unificado": 1430934934402498742,
        "canal_logs": 1430935700387270898,
        "modulos_ativos": ["ti"],

        "ti": {
            "cargo_ti":   1430932068484780184,
            "canal_logs": 1430935700387270898,
        },
    },
}
