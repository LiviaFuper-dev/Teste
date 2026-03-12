# caveira-ti.py
import os
import io
import asyncio
import discord
from discord.ext import commands
import datetime
from dotenv import load_dotenv

# postN.py
import requests

load_dotenv()
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL_TI")

def webPost(payload: dict):
    """
    Envia dados do formulário para o N8N
    """
    try:
        response = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"[WARN] Erro ao enviar dados para N8N: {e}")
        return False

# ===== CONFIG GERAL =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
THREAD_AUTO_ARCHIVE_MINUTES = 1440  # 24h
# =========================

# ====== Configurações por GUILD (IDs reais) ======
SERVIDORES = {
    1407051681421594806: {  # Primeiro servidor (real)
        "nome": "MLR",
        "canal_suporte": 1429906363164790815,
        "cargo_ti": 1415390806541598831,
        "canal_logs": 1429922070036217977
    },
    1409995330795081738: {  # Segundo servidor (real)
        "nome": "FUPER",
        "canal_suporte": 1430934934402498742,
        "cargo_ti": 1430932068484780184,
        "canal_logs": 1430935700387270898
    }
}
# ==================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
motivo = ""

def get_config_for_guild(guild_id: int):
    return SERVIDORES.get(guild_id)


def resolve_cargo_ti(guild: discord.Guild, cfg: dict):
    """Resolve o cargo de T.I por ID ou nome aproximado."""
    expected_id = cfg.get("cargo_ti")
    if expected_id:
        role = guild.get_role(expected_id)
        if role:
            return role, "id"
    for role in guild.roles:
        name = (role.name or "").lower()
        if any(k in name for k in ["ti", "t.i", "tecnico", "suporte"]):
            return role, f"fallback_name:{role.name}"
    return None, "not_found"


def member_has_ti(member: discord.Member, cfg: dict) -> bool:
    """Verifica se o membro tem o cargo de T.I (por id ou por fallback)."""
    expected_id = cfg.get("cargo_ti")
    if expected_id:
        r = member.guild.get_role(expected_id)
        if r and r in member.roles:
            return True
    for r in member.roles:
        if any(k in (r.name or "").lower() for k in ["ti", "t.i", "tecnico", "suporte"]):
            return True
    return False


async def remove_non_ti_members(channel: discord.Thread, guild: discord.Guild, cargo_role: discord.Role):
    """
    Remove (expulsa) todos os membros da thread que NÃO têm o cargo de T.I.
    Retorna lista de display_names removidos.
    """
    removed = []
    failed = []

    # short wait so the thread membership is stable
    await asyncio.sleep(0.15)

    # channel.members may be ThreadMember; convert to Member via guild.get_member/fetch_member
    members_snapshot = list(channel.members)
    for tm in members_snapshot:
        try:
            member = guild.get_member(tm.id) or await guild.fetch_member(tm.id)
        except Exception:
            member = None

        if not member:
            continue
        # skip bots and keep T.I.
        if member.bot:
            continue
        if cargo_role and cargo_role in member.roles:
            continue

        # attempt removal
        try:
            await channel.remove_user(member)
            removed.append(member.display_name)
            print(f"[DEBUG] removido {member.display_name} da thread {channel.name}")
        except Exception as e:
            failed.append((getattr(member, "display_name", str(member.id)), str(e)))
            print(f"[ERROR] falha ao remover {getattr(member, 'display_name', str(member.id))}: {e}")

    return removed, failed


# === Modal e View (mantive suas classes originais inalteradas) ===
class DescricaoModal(discord.ui.Modal):
    def __init__(self, nivel: str):
        super().__init__(title=f"Descreva o problema — {nivel}")
        self.nivel = nivel
        self.descricao = discord.ui.TextInput(
            label="Descrição do problema",
            style=discord.TextStyle.long,
            placeholder="Descreva brevemente o problema e o equipamento afetado.",
            required=True,
            max_length=1900
        )
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await criar_chamado(interaction, self.nivel, self.descricao.value)


class UrgenciaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🟢 Baixo", style=discord.ButtonStyle.success)
    async def baixo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DescricaoModal("Baixo"))

    @discord.ui.button(label="🔵 Médio", style=discord.ButtonStyle.primary)
    async def medio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DescricaoModal("Médio"))

    @discord.ui.button(label="🔴 Alto", style=discord.ButtonStyle.danger)
    async def alto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DescricaoModal("Alto"))


# === criar_chamado (igual ao seu original) ===
async def criar_chamado(interaction: discord.Interaction, nivel: str, descricao: str = None):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    global motivo
    motivo = descricao
    if guild is None or channel is None:
        await interaction.followup.send("⚠️ Não foi possível identificar servidor/canal.", ephemeral=True)
        return

    cfg = get_config_for_guild(guild.id)
    if cfg is None:
        await interaction.followup.send("⚠️ Este servidor não está configurado.", ephemeral=True)
        return

    cargo_ti, _ = resolve_cargo_ti(guild, cfg)
    nome_topico = f"{nivel} - {interaction.user.display_name}"

    try:
        thread = await channel.create_thread(
            name=nome_topico,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Não foi possível criar o tópico. Verifique as permissões do bot (Create Private Threads / Manage Threads).",
            ephemeral=True
        )
        return
    except Exception as e:
        print(f"Erro ao criar thread em {cfg['nome']}: {e}")
        await interaction.followup.send("❌ Erro ao criar tópico. Contate o administrador.", ephemeral=True)
        return

    # ==== garantir que o BOT fique membro da thread para evitar "auto-delete" do Discord ====
    try:
        await thread.join()
        await asyncio.sleep(0.2)
        # envia mensagem âncora para garantir presença
        print(f"[DEBUG] Bot entrou/anunciou na thread: {thread.name} ({thread.id})")
    except Exception as e:
        # fallback para add_user(bot.user)
        print(f"[WARN] thread.join()/send falhou: {e} — tentando add_user(bot.user) como fallback.")
        try:
            await thread.add_user(bot.user)
            await asyncio.sleep(0.2)
            print(f"[DEBUG] add_user(bot.user) succeed for thread {thread.name} ({thread.id})")
        except Exception as e2:
            print(f"[WARN] add_user(bot.user) falhou também: {e2}")

    # tenta garantir que o solicitante também esteja na thread
    try:
        await thread.add_user(interaction.user)
    except Exception:
        pass

    # debug: listar membros atuais da thread
    try:
        member_names = [getattr(m, "display_name", str(m.id)) for m in thread.members]
        print(f"[DEBUG] membros na thread após criação: {member_names}")
    except Exception:
        pass

    # === ADIÇÃO: enviar a descrição do usuário COMO MENSAGEM NA THREAD
    if descricao:
        try:
            quoted_initial = f"**Solicitação de {interaction.user.display_name}:**\n{descricao}"
            await thread.send(quoted_initial)
        except Exception as e:
            print(f"[WARN] não consegui enviar a mensagem inicial do solicitante na thread: {e}")

    color = discord.Color.green() if nivel == "Baixo" else discord.Color.red() if nivel == "Alto" else discord.Color.gold()
    descricao_block = ""
    if descricao:
        quoted = "\n".join("> " + line for line in descricao.splitlines())
        descricao_block = f"**Descrição do solicitante:**\n{quoted}\n\n"

    embed = discord.Embed(
        title="🧰 Chamado aberto",
        description=(
            f"{descricao_block}"
            f"📊 **Nível de urgência:** **{nivel}**\n\n"
            f"Olá {interaction.user.display_name}, por favor, acompanhe este chamado.\n\n"
            "💬 **Todos os participantes podem conversar normalmente.**\n"
            "🔒 **Somente o T.I. pode arquivar ou executar comandos administrativos.**\n\n"
            f"⏳ Este tópico será arquivado automaticamente após 24h de inatividade."
        ),
        color=color
    )

    try:
        await thread.send(embed=embed)
        if cargo_ti:
            await thread.send(cargo_ti.mention, allowed_mentions=discord.AllowedMentions(roles=True))
    except Exception as e:
        print(f"Erro ao enviar mensagens na thread: {e}")

    await interaction.followup.send(f"✅ Chamado criado: {thread.mention}", ephemeral=True)


# ============================================================== 
# === A PARTIR DAQUI: Form + integração !logs ===
# ==============================================================

class LogsFormView(discord.ui.View):
    """
    View exibida no tópico quando !logs é executado.
    Contém selects para Empresa, Tipo e Nível e um botão Confirmar.
    """
    def __init__(self, cfg: dict, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.cfg = cfg
        # defaults
        self.selected_empresa = None
        self.selected_tipo = "Hardware"
        self.selected_nivel = "Baixo"
        self.form_response = None  # será preenchido quando confirmado

    @discord.ui.select(
        placeholder="Empresa (Fuper / Mlr Advogados)",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Fuper", value="fuper", description="Fuper"),
            discord.SelectOption(label="Mlr Advogados", value="mlr_advogados", description="Mlr Advogados"),
        ]
    )
    async def empresa_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_empresa = select.values[0]
        # resposta curta e ephemeral para confirmar seleção
        try:
            display = "Fuper" if self.selected_empresa == "fuper" else "Mlr Advogados"
            await interaction.response.send_message(f"Empresa selecionada: **{display}**", ephemeral=True)
        except Exception:
            pass

    @discord.ui.select(
        placeholder="Tipo de problema (Hardware / Software / Suporte)",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Hardware", value="Hardware", description="Problemas de hardware"),
            discord.SelectOption(label="Software", value="Software", description="Problemas de software"),
            discord.SelectOption(label="Suporte", value="Suporte", description="Solicitação de suporte")
        ]
    )
    async def tipo_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_tipo = select.values[0]
        try:
            await interaction.response.send_message(f"Tipo selecionado: **{self.selected_tipo}**", ephemeral=True)
        except Exception:
            pass

    @discord.ui.select(
        placeholder="Nível real do problema (Baixo / Médio / Alto)",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Baixo", value="Baixo"),
            discord.SelectOption(label="Médio", value="Médio"),
            discord.SelectOption(label="Alto", value="Alto")
        ]
    )
    async def nivel_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_nivel = select.values[0]
        try:
            await interaction.response.send_message(f"Nível selecionado: **{self.selected_nivel}**", ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="✅ Confirmar e gerar logs", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        """
        Confirmação — somente membros com cargo T.I podem confirmar.
        Ao confirmar, preenche self.form_response e EXECUTA o processamento
        (gera log, remove só quem NÃO é T.I e apaga a thread).
        """
        guild = interaction.guild
        member = interaction.user
        expected_role_id = self.cfg.get("cargo_ti")
        has_role = False
        if expected_role_id:
            for r in member.roles:
                if getattr(r, "id", None) == expected_role_id:
                    has_role = True
                    break
        if not has_role:
            for r in member.roles:
                if any(k in (r.name or "").lower() for k in ["ti", "t.i", "tecnico", "suporte"]):
                    has_role = True
                    break

        if not has_role:
            try:
                await interaction.response.send_message("❌ Você precisa do cargo de T.I para confirmar.", ephemeral=True)
            except Exception:
                pass
            return

        # valida que empresa/tipo/nivel foram selecionados
        if not self.selected_empresa or not self.selected_tipo or not self.selected_nivel:
            try:
                await interaction.response.send_message("❌ Preencha Empresa, Tipo e Nível antes de confirmar.", ephemeral=True)
            except Exception:
                pass
            return

        # preenche form_response
        self.form_response = {
            "empresa": self.selected_empresa,
            "tipo": self.selected_tipo,
            "nivel_real_problema": self.selected_nivel,
            "confirmado_por": member.display_name
        }

        # confirma para o usuário antes de executar (ephemeral)
        try:
            await interaction.response.send_message("Formulário recebido. Gerando logs e finalizando o chamado...", ephemeral=True)
        except Exception:
            pass

        # chama rotina que faz tudo (usa interaction como contexto)
        try:
            cargo_role = None
            expected_role_id = self.cfg.get("cargo_ti")
            if expected_role_id:
                cargo_role = guild.get_role(expected_role_id)
            if cargo_role is None:
                for r in guild.roles:
                    if any(k in (r.name or "").lower() for k in ["ti", "t.i", "tecnico", "suporte"]):
                        cargo_role = r
                        break

            await process_and_finalize_logs_interaction(interaction, guild, interaction.channel, self.cfg, cargo_role, self.form_response)
        except Exception as e:
            print(f"[ERROR] Erro na finalização via botão: {e}")
            try:
                await interaction.followup.send(f"❌ Erro ao finalizar o chamado: {e}", ephemeral=True)
            except Exception:
                pass

        try:
            self.stop()
        except Exception:
            pass


# Versão que usa interaction para contexto — realiza log, envia para canal, remove membros (NÃO remove T.I) e APAGA a thread.
async def process_and_finalize_logs_interaction(interaction: discord.Interaction, guild: discord.Guild, channel: discord.Thread, cfg: dict, cargo_ti: discord.Role, form_response: dict):
    # coleta mensagens (inclui resposta do formulário no topo)
    log_text = f"Log da Thread: {channel.name}\n\n"
    log_text += "=== Resposta do formulário (T.I) ===\n"
    for k, v in form_response.items():
        log_text += f"{k}: {v}\n"
    log_text += "\n=== Mensagens da Thread ===\n\n"

    try:
        async for msg in channel.history(oldest_first=True, limit=None):
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            log_text += f"[{timestamp}] {msg.author.display_name}: {msg.content}\n"
            for attach in msg.attachments:
                log_text += f"[Anexo] {attach.url}\n"
    except Exception as e:
        print(f"[WARN] erro ao ler histórico da thread: {e}")

    logs_canal = guild.get_channel(cfg["canal_logs"])
    if not logs_canal:
        print("[DEBUG] canal de logs não encontrado -> abortando")
        try:
            await interaction.followup.send("⚠️ Canal de logs não encontrado.", ephemeral=True)
        except Exception:
            pass
        return

    log_file = discord.File(io.BytesIO(log_text.encode("utf-8")), filename=f"log_{channel.name}.txt")
    try:
        await logs_canal.send(content=f"📁 Log do chamado `{channel.name}`", file=log_file)
        print("[DEBUG] log enviado para canal de logs com sucesso.")
    except Exception as e:
        print(f"[ERROR] Falha ao enviar log para canal de logs: {e}")
        try:
            await interaction.followup.send(f"⚠️ Não foi possível enviar o log para o canal: {e}", ephemeral=True)
        except Exception:
            pass
        return

    # ==================================================
    # === ENVIO DOS DADOS PARA O N8N ===================
    # ==================================================
    try:
        payload = {
            "empresa": form_response.get("empresa"),
            "tipo": form_response.get("tipo"),
            "nivel_real_problema": form_response.get("nivel_real_problema"),
            "confirmado_por": form_response.get("confirmado_por"),
            "thread": channel.name,
            "thread_id": channel.id,
            "canal_logs": logs_canal.id,
            "servidor": cfg.get("nome"),
            "guild_id": guild.id,
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "motivo": motivo
        }
        print(payload)
        webPost(payload)
        print("[DEBUG] Dados enviados para N8N com sucesso.")
    except Exception as e:
        print(f"[WARN] Falha ao enviar dados para N8N: {e}")

    # Remove membros — NUNCA removemos quem tem cargo de T.I (cargo_ti)
    removed = []
    failed = []
    for thread_member in list(channel.members):
        try:
            member_obj = guild.get_member(thread_member.id) or await guild.fetch_member(thread_member.id)
        except Exception:
            member_obj = None

        if not member_obj:
            continue
        if member_obj.bot:
            continue
        if cargo_ti and cargo_ti in member_obj.roles:
            continue

        try:
            await channel.remove_user(member_obj)
            removed.append(member_obj.display_name)
            print(f"[DEBUG] removido {member_obj.display_name} da thread")
        except Exception as e:
            failed.append((member_obj.display_name, str(e)))
            print(f"[ERROR] erro ao remover {member_obj.display_name}: {e}")

    summary = f"📁 Log salvo. Removidos: {len(removed)} usuários."
    if failed:
        summary += f" Erros: {len(failed)} (veja terminal)."

    try:
        await interaction.followup.send(summary, ephemeral=False)
    except Exception:
        try:
            await channel.send(summary)
        except Exception:
            pass
    print(f"[DEBUG] removidos: {removed}, falhas: {failed}")

    # 🧹 EXCLUIR O TÓPICO APÓS GERAR LOG
    try:
        await channel.delete(reason="Tópico finalizado após geração de log.")
        print(f"[DEBUG] Tópico '{channel.name}' excluído com sucesso.")
    except Exception as e:
        print(f"[ERROR] Falha ao excluir o tópico '{channel.name}': {e}")


# === gerar_logs (agora: expulsa não-T.I ANTES de enviar o formulário) ===
@bot.command(name="logs")
async def gerar_logs(ctx: commands.Context):
    """
    Fecha um tópico de suporte.

    Remove colaborador do tópico
    e envia um formulário técnico para o TI.
    lança os logs em um canal de logs e o formulário 
    para o Clickup.
    """
    guild = ctx.guild
    channel = ctx.channel
    print("=== !logs chamado ===")

    if guild is None or channel is None:
        await ctx.reply("⚠️ Não foi possível identificar servidor/canal.", mention_author=False)
        return

    cfg = get_config_for_guild(guild.id)
    if cfg is None:
        print("[DEBUG] Nenhuma config encontrada para este guild.id")
        return

    cargo_ti_role = guild.get_role(cfg["cargo_ti"]) if cfg.get("cargo_ti") else None
    if cargo_ti_role is None:
        for r in guild.roles:
            if any(k in (r.name or "").lower() for k in ["ti", "t.i", "tecnico", "suporte"]):
                cargo_ti_role = r
                break

    if cargo_ti_role is None:
        print("[DEBUG] cargo_ti não encontrado -> abortando")
        return

    if not isinstance(channel, discord.Thread):
        print("[DEBUG] comando !logs chamado fora de uma thread -> abortando")
        return

    if cargo_ti_role not in ctx.author.roles:
        print("[DEBUG] Autor NÃO tem o cargo_ti -> abortando")
        return

    # 1) Expulsa IMEDIATAMENTE todos que não são T.I (incluindo o solicitante)
    removed, failed = await remove_non_ti_members(channel, guild, cargo_ti_role)

    # feedback para quem executou o comando (TI)
    try:
        if failed:
            summary += f" Erros: {len(failed)} (veja logs)."
        await ctx.reply(summary, mention_author=False)
    except Exception:
        pass
    print(f"[DEBUG] removidos antes do form: {removed}, falhas: {failed}")

    # 2) Agora que só T.I (e bot) permanecem, enviamos o formulário (visível apenas a T.I)
    view = LogsFormView(cfg=cfg, timeout=120.0)
    try:
        await ctx.reply("Por favor, selecione a Empresa, o Tipo e o Nível real do problema e clique em **Confirmar**.", view=view, mention_author=False)
    except Exception as e:
        print(f"[WARN] não foi possível enviar a view no tópico: {e}")
        try:
            await ctx.author.send("Por favor, selecione a Empresa, o Tipo e o Nível real do problema e clique em Confirmar.", view=view)
        except Exception:
            await ctx.reply("❌ Não foi possível abrir o formulário aqui nem em DM.", mention_author=False)
            return

    # espera até o T.I confirmar (ou timeout)
    await view.wait()

    if not view.form_response:
        try:
            await ctx.reply("❌ Formulário não foi preenchido. Operação abortada.", mention_author=False)
        except Exception:
            pass
        print("[DEBUG] formulário não preenchido -> abortando !logs")
        return

    # A finalização (remoção de qualquer não-T.I restante e delete) será feita pelo handler do botão.
    print("=== !logs finalizado (form enviado e aguardando confirmação) ===")


# === on_ready ===
@bot.event
async def on_ready():
    print(f"✅ Bot conectado como {bot.user}")

    for guild_id, cfg in SERVIDORES.items():
        nome = cfg.get("nome", str(guild_id))
        canal = bot.get_channel(cfg.get("canal_suporte"))

        if canal is None:
            print(f"❌ Canal de suporte não encontrado em {nome}.")
            continue

        try:
            async for msg in canal.history(limit=200):
                if msg.author == bot.user:
                    try:
                        await msg.delete()
                    except Exception as e:
                        print(f"⚠️ Não foi possível deletar mensagem antiga: {e}")
        except Exception as e:
            print(f"⚠️ Erro ao varrer histórico em {nome}: {e}")

        try:
            embed = discord.Embed(
                title="🛠️ Bem-vindo ao Suporte Técnico",
                description=(
                    "Use este canal para **relatar problemas técnicos** nos equipamentos da empresa, "
                    "como falhas de som, conexão, travamentos ou outros. Nossa equipe está pronta para ajudar!\n\n"
                    "Antes de enviar o chamado, selecione abaixo o nível que **melhor representa sua situação**:\n\n"
                    "🟢 **Baixo:** pequenas dúvidas ou erros que não impedem o trabalho "
                    "(ex: configurações, orientações simples)\n"
                    "🔵 **Médio:** falhas que dificultam, mas não impedem o trabalho "
                    "(ex: lentidão, ruído, travamentos ocasionais)\n"
                    "🔴 **Alto:** situações críticas onde não é possível trabalhar "
                    "(ex: microfone, computador ou rede inoperante)\n\n"
                    "⚙️ Clique em um botão abaixo para abrir seu chamado."
                ),
                color=discord.Color.blurple()
            )

            mensagem = await canal.send(embed=embed, view=UrgenciaView(), silent=True)
            try:
                await mensagem.pin()
            except Exception:
                pass

        except Exception as e:
            print(f"❌ Erro ao enviar mensagem inicial em {nome}: {e}")


# === EXECUÇÃO ===
if __name__ == "__main__":
    if DISCORD_TOKEN == "SEU_TOKEN_AQUI":
        print("ATENÇÃO: defina a variável de ambiente DISCORD_TOKEN com o token do bot.")
    bot.run(DISCORD_TOKEN)
