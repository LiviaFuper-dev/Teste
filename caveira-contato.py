# -*- coding: utf-8 -*-
import os
import datetime
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import aiohttp
import re
import asyncio

# ======================================================
# CONFIGURAÇÕES (PREENCHA OS IDS)
# ======================================================
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = 1407051681421594806
CANAL_ID = 1463886633999794218
TARGET_USER_ID = 287745016003035137

# Usuário que PERMANECE no tópico após !contato (além do próprio bot)
KEEPER_USER_ID = 287745016003035137
KEEPER_ROLE_ID = 1415390806541598831  # Cargo de segurança para não expulsar por engano

THREAD_AUTO_ARCHIVE_MINUTES = 1440

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

# ======================================================
# MENSAGEM DE INATIVIDADE (edite aqui)
# ======================================================
INACTIVITY_TIMEOUT_SECONDS = 86400  # 24 horas
INACTIVITY_MESSAGE = (
    "Eii! Atualização sobre a sua busca: :hourglass:\n\n"
    "O sistema ainda está rodando a varredura para localizar os contatos desse cliente.\n\n"
    "**Por que isso acontece?** Alguns clientes possuem cadastros muito desatualizados ou difíceis de cruzar nas bases públicas. "
    "Para não te entregar um número errado e fazer você perder tempo ligando para terceiros, nossos algoritmos estão aprofundando "
    "a busca em fontes alternativas.\n\n"
    "**O que fazer agora?** Pode focar nas suas outras demandas, não precisa se preocupar ou abrir um novo chamado. "
    "O robô continua trabalhando nesse caso em segundo plano. Assim que batermos o contato quente dele, te avisamos aqui na hora! "
    ":scales: :rocket:"
)

# ======================================================
# MENSAGEM DE CONCLUSÃO (enviada pelo !contato)
# ======================================================
CONTATO_CONCLUSAO_MESSAGE = (
    "Opa! Busca concluída com sucesso. :white_check_mark:\n\n"
    "Fizemos uma varredura completa e conseguimos extrair **TODOS** os números de telefone possíveis desse cliente. "
    "O dossiê de contatos tá na sua mão!\n\n"
    "Agora é com você: bora iniciar as tentativas, cobrar os documentos e dar andamento nesse processo para ganharmos logo esse caso. :scales:\n\n"
    "Para facilitar a sua vida, lembre que todos os números resgatados ficam salvos e disponíveis 24h por dia neste link: :point_down:\n\n"
    "https://app.clickup.com/9011605202/v/li/901112971241\n\n"
    "Bom trabalho e vamos pra cima! :rocket:"
)

# Tempo de espera antes de fechar o tópico após !contato (12 horas em segundos)
CONTATO_CLOSE_DELAY_SECONDS = 43200

# ======================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ================================
# ARMAZENAMENTO (pronto pro n8n)
# ================================
CONTACT_SUBMISSIONS: list[dict] = []

# ================================
# CONTROLE DE INATIVIDADE
# Formato: { thread_id: {"last_activity": datetime} }
# ================================
THREAD_ACTIVITY: dict[int, dict] = {}

# ================================
# VALIDAÇÃO CPF (FORMATO)
# ================================
CPF_REGEX = re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$")

def validar_formato_cpf(cpf: str) -> bool:
    """
    Valida o formato do CPF.
    Aceita apenas o padrão: xxx.xxx.xxx-xx
    """
    return bool(CPF_REGEX.match(cpf))

# ================================
# ENVIO N8N
# ================================
async def enviar_para_n8n(payload: dict) -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                N8N_WEBHOOK_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                status = resp.status
                content = None
                try:
                    content = await resp.json()
                    result = {"status": status, "ok": (status == 200), "json": content}
                except aiohttp.ContentTypeError:
                    text = await resp.text()
                    result = {"status": status, "ok": (status == 200), "text": text}

                if not result["ok"]:
                    print(f"[N8N ERRO] {status} - {result.get('text') or result.get('json')}")
                else:
                    print("[N8N OK] Payload enviado com sucesso")
                    print("[N8N RESPOSTA]", result)

                return result
    except asyncio.TimeoutError:
        print("[N8N ERRO] Timeout ao chamar webhook")
        return {"status": None, "ok": False, "error": "timeout"}
    except Exception as e:
        print(f"[N8N ERRO] Exceção ao chamar webhook: {e}")
        return {"status": None, "ok": False, "error": str(e)}

# ================================
# MODAL PRINCIPAL
# ================================
class ContatoModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Recuperar Contato")

        self.nome = discord.ui.TextInput(
            label="Nome do contato",
            placeholder="Nome completo",
            required=True,
            max_length=200
        )

        self.cpf = discord.ui.TextInput(
            label="CPF do contato - coloque no formato correto",
            placeholder="000.000.000-00",
            required=True,
            max_length=20
        )

        self.add_item(self.nome)
        self.add_item(self.cpf)

    async def on_submit(self, interaction: discord.Interaction):
        cpf = self.cpf.value.strip()

        if not validar_formato_cpf(cpf):
            await interaction.response.send_message(
                "❌ **CPF inválido!**\n\nUse o formato correto: `000.000.000-00`",
                ephemeral=True
            )
            return

        await criar_thread_contato(
            interaction,
            self.nome.value.strip(),
            cpf
        )

# ================================
# VIEW COM BOTÃO
# ================================
class ContatoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📞 RECUPERAR TELEFONE 📞",
        style=discord.ButtonStyle.success,
        custom_id="botao_contato"
    )
    async def contato(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ContatoModal())

# ================================
# TASK: VERIFICAÇÃO DE INATIVIDADE
# Roda a cada 60 segundos e verifica
# quais threads ultrapassaram o timeout
# ================================
@tasks.loop(seconds=60)
async def verificar_inatividade():
    agora = datetime.datetime.utcnow()
    threads_para_remover = []

    for thread_id, info in THREAD_ACTIVITY.items():
        delta = (agora - info["last_activity"]).total_seconds()
        if delta >= INACTIVITY_TIMEOUT_SECONDS:
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                continue

            thread = guild.get_thread(thread_id)
            if thread is None:
                threads_para_remover.append(thread_id)
                continue

            try:
                await thread.send(INACTIVITY_MESSAGE)
                # Reseta o timer para não spammar a mesma mensagem
                THREAD_ACTIVITY[thread_id]["last_activity"] = datetime.datetime.utcnow()
            except Exception as e:
                print(f"[INATIVIDADE] Erro ao enviar mensagem na thread {thread_id}: {e}")

    for tid in threads_para_remover:
        THREAD_ACTIVITY.pop(tid, None)

# ================================
# FECHAR TÓPICO APÓS DELAY
# Chamado após o comando !contato
# ================================
async def fechar_topico_apos_delay(thread: discord.Thread):
    """Aguarda CONTATO_CLOSE_DELAY_SECONDS e então fecha (deleta) o tópico."""
    await asyncio.sleep(CONTATO_CLOSE_DELAY_SECONDS)

    # Verifica se o tópico ainda existe antes de tentar deletar
    try:
        # Tenta buscar o tópico atualizado para confirmar que ainda existe
        guild = bot.get_guild(GUILD_ID)
        if guild:
            t = guild.get_thread(thread.id)
            if t is None:
                print(f"[AUTO-CLOSE] Tópico {thread.id} já foi deletado, nada a fazer.")
                return
        await thread.delete()
        print(f"[AUTO-CLOSE] Tópico {thread.id} fechado automaticamente após {CONTATO_CLOSE_DELAY_SECONDS}s.")
    except discord.NotFound:
        print(f"[AUTO-CLOSE] Tópico {thread.id} não encontrado (já deletado).")
    except Exception as e:
        print(f"[AUTO-CLOSE] Erro ao fechar tópico {thread.id}: {e}")

# ================================
# LÓGICA PRINCIPAL
# ================================
async def criar_thread_contato(
    interaction: discord.Interaction,
    nome_contato: str,
    cpf_contato: str
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    channel = interaction.channel

    if not guild or not channel:
        return

    # ── 1. Cria o tópico ──────────────────────────────────────────────────────
    thread = await channel.create_thread(
        name=f"Contato - {interaction.user.display_name}",
        type=discord.ChannelType.private_thread,
        auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
    )

    await thread.add_user(interaction.user)

    target_added = False
    try:
        target = guild.get_member(TARGET_USER_ID) or await guild.fetch_member(TARGET_USER_ID)
        if target:
            await thread.add_user(target)
            target_added = True
    except Exception as e:
        print(f"[WARN] não foi possível adicionar TARGET_USER: {e}")

    # ── 2. Registra a thread no controle de inatividade ───────────────────────
    THREAD_ACTIVITY[thread.id] = {
        "last_activity": datetime.datetime.utcnow()
    }

    # ── 3. Monta e envia o payload ao n8n ─────────────────────────────────────
    payload = {
        "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "guild_id": guild.id,
        "channel_id": channel.id,
        "thread_id": thread.id,
        "author_id": interaction.user.id,
        "author_name": interaction.user.display_name,
        "contact_name": nome_contato,
        "contact_cpf": cpf_contato,
        "target_user_added": target_added,
    }

    CONTACT_SUBMISSIONS.append(payload)

    resp = await enviar_para_n8n(payload)

    # ── 4. Verifica a flag "existe" ────────────────────────────────────────────
    existe = resp.get("ok") and resp.get("json", {}).get("existe", False)

    if existe:
        THREAD_ACTIVITY.pop(thread.id, None)

        try:
            await thread.delete()
        except Exception as e:
            print(f"[WARN] falha ao deletar thread duplicada {thread.id}: {e}")

        try:
            await interaction.user.send(
                "Opa! Tudo bem?:octagonal_sign: \n\nO sistema identificou que a sua última solicitação no puxador é de um contato que **já foi recuperado antes.** \n\n"
                "Como a plataforma encerra chamados duplicados automaticamente para não travar a fila de atendimento de todo mundo, "
                "essa sua solicitação foi fechada, beleza? \n\nMas fica tranquilo que o seu contato tá na mão. "
                "Você pode acessar e encontrar os números dele direto por este link: :point_down: \n\n"
                "https://app.clickup.com/9011605202/v/li/901112971241\n\n"
                "**Dica: Para economizar o seu próprio tempo, dê sempre uma conferida rápida, pesquisando na lupa, "
                "se o contato já não está na base antes de puxar!** :rocket:"
            )
        except discord.Forbidden:
            print(f"[WARN] não foi possível enviar DM para {interaction.user} (DMs desativadas)")

        return

    # ── 5. Fluxo normal: contato novo ──────────────────────────────────────────
    if resp.get("ok"):
        text_to_show = "✅ CPF não existe no sistema."
    else:
        text_to_show = "Falha ao enviar para n8n."

    await thread.send(
        f"📇 **Contato registrado**\n\n"
        f"**Nome:** {nome_contato}\n"
        f"**CPF:** {cpf_contato}\n\n"
        f"{text_to_show}"
    )

# ================================
# EVENTO: MENSAGEM ENVIADA
# Atualiza o timestamp de atividade
# ================================
@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if message.channel.id in THREAD_ACTIVITY:
        THREAD_ACTIVITY[message.channel.id]["last_activity"] = datetime.datetime.utcnow()

    await bot.process_commands(message)

# ================================
# COMANDO !contato
# 1. Remove todos do tópico exceto bot e KEEPER_USER_ID
# 2. Envia mensagem de conclusão
# 3. Aguarda 12h e fecha o tópico
# ================================
@bot.command(name="contato")
async def contato_cmd(ctx: commands.Context):
    channel = ctx.channel

    if channel.type not in (
        discord.ChannelType.private_thread,
        discord.ChannelType.public_thread,
        discord.ChannelType.news_thread,
    ):
        try:
            await ctx.author.send(
                "O comando `!contato` só pode ser usado dentro de um tópico (thread)."
            )
        except Exception:
            try:
                await ctx.message.add_reaction("❌")
            except Exception:
                pass
        return

    is_contato_thread = False
    try:
        if channel.name.startswith("Contato - "):
            is_contato_thread = True
        elif channel.parent and channel.parent.id == CANAL_ID:
            is_contato_thread = True
    except Exception:
        pass

    if not is_contato_thread:
        try:
            await ctx.author.send(
                "O comando `!contato` só pode ser usado em tópicos de contato."
            )
        except Exception:
            try:
                await ctx.message.add_reaction("❌")
            except Exception:
                pass
        return

    # ── 1. Remove do controle de inatividade ──────────────────────────────────
    THREAD_ACTIVITY.pop(channel.id, None)

    # ── 2. Remove apenas o TARGET_USER_ID do tópico ───────────────────────────
    # (Iterar channel.members em threads privadas retorna ThreadMember,
    #  que não possui .roles — checagem de cargo não funciona nesse contexto.
    #  A abordagem segura é remover apenas quem foi adicionado explicitamente.)
    guild = ctx.guild
    if guild:
        try:
            target = guild.get_member(TARGET_USER_ID) or await guild.fetch_member(TARGET_USER_ID)
            if target:
                await channel.remove_user(target)
                print(f"[!contato] Membro {TARGET_USER_ID} removido do tópico {channel.id}.")
        except Exception as e:
            print(f"[WARN] não foi possível remover TARGET_USER do tópico: {e}")

    # ── 3. Envia a mensagem de conclusão ──────────────────────────────────────
    try:
        await channel.send(CONTATO_CONCLUSAO_MESSAGE)
    except Exception as e:
        print(f"[ERROR] falha ao enviar mensagem de conclusão no tópico {channel.id}: {e}")

    # ── 4. Agenda o fechamento após 12 horas ──────────────────────────────────
    print(f"[!contato] Tópico {channel.id} será fechado em {CONTATO_CLOSE_DELAY_SECONDS}s (12h).")
    asyncio.create_task(fechar_topico_apos_delay(channel))

# ================================
# ON READY
# ================================
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")

    verificar_inatividade.start()
    print(f"⏱️  Task de inatividade iniciada (timeout: {INACTIVITY_TIMEOUT_SECONDS}s / 24h)")

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    canal = guild.get_channel(CANAL_ID)
    if not canal:
        return

    async for msg in canal.history(limit=50):
        if msg.author == bot.user:
            try:
                await msg.delete()
            except Exception:
                pass

    embed = discord.Embed(
        title="📞 Recuperação de telefone de Contato",
        description="Clique no botão abaixo para recuperar o telefone de um contato.",
        color=discord.Color.blurple()
    )

    mensagem = await canal.send(embed=embed, view=ContatoView())
    try:
        await mensagem.pin()
    except Exception:
        pass

# ================================
# EXECUÇÃO
# ================================
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)