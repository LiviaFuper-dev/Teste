"""
modules/ti.py — Módulo de Suporte Técnico (T.I.)

Fluxo:
  - Usuário clica em "Equipamentos" → ephemeral com seleção de urgência
  - Seleciona Baixo / Médio / Alto → modal de descrição
  - Submete → thread privada criada com embed + ping do cargo TI
  - TI digita !logs → remove colaborador, abre formulário (Empresa + Nível),
    gera log .txt, envia N8N, deleta thread
"""

import io
import asyncio
import datetime
import discord
from discord.ext import commands

import config
from utils import n8n as n8n_utils
from utils.thread_utils import safe_join_thread, remove_members_except


# ── Armazenamento local ────────────────────────────────────────────────────────

# Controles auxiliares do fluxo de TI.
# _LOGS_ACTIVE_THREADS evita executar !logs duas vezes ao mesmo tempo na mesma thread.
# _BR_TIMEZONE padroniza datas e logs no horário de Brasília.

_THREAD_MOTIVO: dict[int, str] = {}
_THREAD_SOLICITANTE: dict[int, tuple[int, str]] = {}
_LOGS_ACTIVE_THREADS: set[int] = set()
_BR_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-3))


def _formatar_data_br(ts: datetime.datetime) -> str:
    return ts.astimezone(_BR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def pop_thread_motivo(thread_id: int) -> str:
    return _THREAD_MOTIVO.pop(thread_id, "")


def pop_thread_solicitante(thread_id: int) -> tuple[int, str] | None:
    return _THREAD_SOLICITANTE.pop(thread_id, None)


def register_thread_motivo(thread_id: int, motivo: str) -> None:
    _THREAD_MOTIVO[thread_id] = motivo


# Normaliza a empresa para o payload do N8N/ClickUp.
# O código técnico ("mlr_advogados"/"fuper") é usado pela automação,
# enquanto o label ("MLR"/"FUPER") é usado para exibição.

def _empresa_clickup_ti(guild_id: int) -> tuple[str | None, str | None]:
    raw = str(
        config.SERVIDORES.get(guild_id, {}).get("empresa_clickup")
        or config.SERVIDORES.get(guild_id, {}).get("nome")
        or ""
    ).strip().lower()

    if raw in {"mlr", "mlr_advogados", "mlr advogados"}:
        return "mlr_advogados", "MLR"
    if raw in {"fuper"}:
        return "fuper", "FUPER"
    return None, None


def _empresa_payload_ti(raw: str | None) -> dict:
    raw_norm = str(raw or "").strip().lower()
    if raw_norm in {"mlr", "mlr_advogados", "mlr advogados"}:
        return {
            "empresa": "mlr_advogados",
            "Empresa": "MLR",
            "empresa_label": "MLR",
            "empresa_codigo": "mlr_advogados",
            "empresa_clickup": "mlr_advogados",
        }
    if raw_norm in {"fuper"}:
        return {
            "empresa": "fuper",
            "Empresa": "FUPER",
            "empresa_label": "FUPER",
            "empresa_codigo": "fuper",
            "empresa_clickup": "fuper",
        }
    return {}


# ── Helpers internos ───────────────────────────────────────────────────────────

def _ti_cfg(guild_id: int) -> dict:
    return config.SERVIDORES.get(guild_id, {}).get("ti", {})


def _get_cargo_ti(guild: discord.Guild, guild_id: int) -> discord.Role | None:
    role_id = _ti_cfg(guild_id).get("cargo_ti")
    if role_id:
        role = guild.get_role(role_id)
        if role:
            return role
    for r in guild.roles:
        if any(k in r.name.lower() for k in ["ti", "t.i", "tecnico", "suporte"]):
            return r
    return None

def _get_cargo_equipamentos(guild: discord.Guild, guild_id: int) -> discord.Role | None:
    role_id = _ti_cfg(guild_id).get(
        "cargo_equipamentos",
        getattr(config, "EQUIPAMENTOS_ROLE_ID", None),
    )
    return guild.get_role(role_id) if role_id else None

# ── Modal: descrição do problema ──────────────────────────────────────────────

class DescricaoModal(discord.ui.Modal):
    def __init__(self, nivel: str, original_interaction: discord.Interaction = None):
        super().__init__(title=f"Descreva o problema — {nivel}")
        self.nivel = nivel
        self.original_interaction = original_interaction
        self.descricao = discord.ui.TextInput(
            label="Descrição do problema",
            style=discord.TextStyle.long,
            placeholder="Descreva brevemente o problema e o equipamento afetado.",
            required=True,
            max_length=1900,
        )
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await _criar_chamado(
            interaction, self.nivel, self.descricao.value, self.original_interaction
        )


# ── View: seleção de urgência ─────────────────────────────────────────────────

class UrgenciaView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.original_interaction = original_interaction

    @discord.ui.button(label="🟢 Baixo", style=discord.ButtonStyle.success, custom_id="ti_urgencia_baixo")
    async def baixo(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            DescricaoModal("2 - Baixo", self.original_interaction)
        )

    @discord.ui.button(label="🔵 Médio", style=discord.ButtonStyle.primary, custom_id="ti_urgencia_medio")
    async def medio(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            DescricaoModal("2 - Médio", self.original_interaction)
        )

    @discord.ui.button(label="🔴 Alto", style=discord.ButtonStyle.danger, custom_id="ti_urgencia_alto")
    async def alto(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            DescricaoModal("2 - Alto", self.original_interaction)
        )


# ── Criação do chamado ────────────────────────────────────────────────────────

async def _criar_chamado(
    interaction: discord.Interaction,
    nivel: str,
    descricao: str,
    original_interaction: discord.Interaction = None,
) -> None:
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel

    if not guild or not channel:
        await interaction.followup.send(
            "⚠️ Não foi possível identificar servidor/canal.", ephemeral=True
        )
        return

    if guild.id not in config.SERVIDORES:
        await interaction.followup.send(
            "⚠️ Este servidor não está configurado.", ephemeral=True
        )
        return

    cargo_ti = _get_cargo_ti(guild, guild.id)
    cargo_equipamentos = _get_cargo_equipamentos(guild, guild.id)

    try:
        thread = await channel.create_thread(
            name=f"{nivel} - {interaction.user.display_name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Sem permissão para criar tópico privado.\n"
            "Verifique: **Create Private Threads** e **Manage Threads**.",
            ephemeral=True,
        )
        return
    except Exception as e:
        print(f"[TI] Erro ao criar thread em {guild.name}: {e}")
        await interaction.followup.send("❌ Erro ao criar tópico.", ephemeral=True)
        return

    await safe_join_thread(interaction.client.user, thread)

    try:
        await thread.add_user(interaction.user)
    except Exception:
        pass

    _THREAD_MOTIVO[thread.id] = descricao
    _THREAD_SOLICITANTE[thread.id] = (interaction.user.id, interaction.user.display_name)

    try:
        await thread.send(
            f"**Solicitação de {interaction.user.display_name}:**\n{descricao}"
        )
    except Exception as e:
        print(f"[TI] Erro ao enviar descrição inicial: {e}")

    color = (
        discord.Color.green() if "Baixo" in nivel
        else discord.Color.red() if "Alto" in nivel
        else discord.Color.gold()
    )
    quoted = "\n".join(f"> {line}" for line in descricao.splitlines())

    embed = discord.Embed(
        title="🧰 Chamado aberto",
        description=(
            f"**Descrição do solicitante:**\n{quoted}\n\n"
            f"📊 **Nível de urgência:** **{nivel}**\n\n"
            f"Olá {interaction.user.display_name}, por favor, acompanhe este chamado.\n\n"
            "💬 **Todos os participantes podem conversar normalmente.**\n"
            "🔒 **Somente o T.I. pode arquivar ou executar comandos administrativos.**\n\n"
            "⏳ Este tópico será movido para chamados pendentes após 8 h de inatividade."
        ),
        color=color,
    )

    try:
        await thread.send(embed=embed)
        mention_ids: list[int] = []
        if cargo_equipamentos:
            mention_ids.append(cargo_equipamentos.id)
        else:
            role_id = _ti_cfg(guild.id).get("cargo_equipamentos")
            if role_id:
                mention_ids.append(int(role_id))
                print(f"[TI] Cargo de equipamentos nao encontrado no cache; mencionando por ID {role_id}.")
        mentions = [f"<@&{role_id}>" for role_id in dict.fromkeys(mention_ids)]
        if mentions:
            await thread.send(
                " ".join(mentions),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
    except Exception as e:
        print(f"[TI] Erro ao enviar embed: {e}")

    await interaction.followup.send(
        f"✅ Chamado criado: {thread.mention}", ephemeral=True
    )

    if original_interaction:
        await asyncio.sleep(3)
        try:
            await original_interaction.delete_original_response()
        except Exception:
            pass


# ── Formulário de logs (!logs) ────────────────────────────────────────────────

class LogsFormView(discord.ui.View):
    """
    Formulário exibido no tópico após !logs.
    Selects de Empresa e Nível + botão Confirmar.
    Somente membros com cargo T.I. podem confirmar.
    """

    def __init__(self, guild_id: int = 0, timeout: float = 120.0):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.selected_empresa: str | None = None
        self.selected_nivel: str | None = None
        self.form_response: dict | None = None

    @discord.ui.select(
        placeholder="Empresa (Fuper / Mlr Advogados)",
        min_values=1,
        max_values=1,
        custom_id="ti_logs_empresa",
        options=[
            discord.SelectOption(label="Fuper",         value="fuper"),
            discord.SelectOption(label="Mlr Advogados", value="mlr_advogados"),
        ],
    )
    async def empresa_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        self.selected_empresa = select.values[0]
        label = "Fuper" if self.selected_empresa == "fuper" else "Mlr Advogados"
        try:
            await interaction.response.send_message(
                f"Empresa selecionada: **{label}**", ephemeral=True
            )
        except Exception:
            pass

    @discord.ui.select(
        placeholder="Nível real do problema",
        min_values=1,
        max_values=1,
        custom_id="ti_logs_nivel",
        options=[
            discord.SelectOption(label="Baixo", value="Baixo"),
            discord.SelectOption(label="Médio", value="Médio"),
            discord.SelectOption(label="Alto",  value="Alto"),
        ],
    )
    async def nivel_select(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        self.selected_nivel = select.values[0]
        try:
            await interaction.response.send_message(
                f"Nível selecionado: **{self.selected_nivel}**", ephemeral=True
            )
        except Exception:
            pass

    @discord.ui.button(
        label="✅ Confirmar e gerar logs",
        style=discord.ButtonStyle.danger,
        custom_id="ti_logs_confirmar",
    )
    async def confirmar(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        member = interaction.user

        cargo_ti = _get_cargo_ti(guild, guild.id)
        if not cargo_ti or cargo_ti not in member.roles:
            try:
                await interaction.response.send_message(
                    "❌ Você precisa do cargo de T.I. para confirmar.", ephemeral=True
                )
            except Exception:
                pass
            return

        if not self.selected_empresa or not self.selected_nivel:
            try:
                await interaction.response.send_message(
                    "❌ Selecione **Empresa** e **Nível** antes de confirmar.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        self.form_response = {
            "empresa":             self.selected_empresa,
            "nivel_real_problema": self.selected_nivel,
            "confirmado_por":      member.display_name,
        }

        try:
            await interaction.response.send_message(
                "⏳ Formulário recebido. Gerando logs e finalizando o chamado...",
                ephemeral=True,
            )
        except Exception:
            pass

        try:
            cfg = _ti_cfg(guild.id)
            await _process_and_finalize(
                interaction, guild, interaction.channel, cfg, cargo_ti, self.form_response
            )
        except Exception as e:
            print(f"[TI] Erro na finalização: {e}")
            try:
                await interaction.followup.send(
                    f"❌ Erro ao finalizar o chamado: {e}", ephemeral=True
                )
            except Exception:
                pass

        self.stop()


# ── Finalização: log + N8N + remoção + delete ─────────────────────────────────

async def _process_and_finalize(
    interaction: discord.Interaction,
    guild: discord.Guild,
    channel: discord.Thread,
    cfg: dict,
    cargo_ti: discord.Role,
    form_response: dict,
) -> None:

    # 1. Coleta histórico de mensagens
    motivo_historico = ""
    log_text = (
        f"Log da Thread: {channel.name}\n\n"
        f"=== Formulário (T.I.) ===\n"
    )
    for k, v in form_response.items():
        log_text += f"{k}: {v}\n"
    log_text += "\n=== Mensagens da Thread ===\n\n"

    try:
        async for msg in channel.history(oldest_first=True, limit=None):
            ts = _formatar_data_br(msg.created_at)
            if not motivo_historico and msg.content.startswith("**Solicitação de "):
                motivo_historico = msg.content.split("**\n", 1)[-1].strip()
            log_text += f"[{ts}] {msg.author.display_name}: {msg.content}\n"
            for att in msg.attachments:
                log_text += f"[Anexo] {att.url}\n"
    except Exception as e:
        print(f"[TI] Erro ao ler histórico: {e}")

    # 2. Envia log para o canal de logs
    canal_logs_id = cfg.get("canal_logs")
    logs_canal = guild.get_channel(canal_logs_id) if canal_logs_id else None

    if not logs_canal:
        if interaction:
            try:
                await interaction.followup.send(
                    "⚠️ Canal de logs não encontrado. Verifique `config.py`.", ephemeral=True
                )
            except Exception:
                pass
        print(f"[TI] Canal de logs não encontrado (guild {guild.id})")
        return

    log_file = discord.File(
        io.BytesIO(log_text.encode("utf-8")),
        filename=f"log_{channel.name}.txt",
    )
    try:
        await logs_canal.send(
            content=f"📁 Log do chamado `{channel.name}`", file=log_file
        )
        print(f"[TI] Log enviado para #{logs_canal.name}")
    except Exception as e:
        print(f"[TI] Erro ao enviar log: {e}")
        if interaction:
            try:
                await interaction.followup.send(
                    f"⚠️ Não foi possível enviar o log: {e}", ephemeral=True
                )
            except Exception:
                pass
        return

    # 3. Envia dados para o N8N
    motivo = _THREAD_MOTIVO.pop(channel.id, "") or motivo_historico
    if config.N8N_WEBHOOK_TI:
        chat_discord = datetime.datetime.now(_BR_TIMEZONE)
        empresa_payload = _empresa_payload_ti(form_response.get("empresa"))
        empresa_nome = empresa_payload.get("Empresa") or config.SERVIDORES.get(guild.id, {}).get("nome")
        payload = {
            **empresa_payload,
            "nivel_real_problema": form_response.get("nivel_real_problema"),
            "confirmado_por":      form_response.get("confirmado_por"),
            "thread":              channel.name,
            "thread_id":           channel.id,
            "canal_logs":          canal_logs_id,
            "servidor":            config.SERVIDORES.get(guild.id, {}).get("nome"),
            "empresa_selecionada": empresa_nome,
            "guild_id":            guild.id,
            "timestamp":           datetime.datetime.utcnow().isoformat() + "Z",
            "motivo":              motivo,
            "Chamados":            motivo,
            "chamados":            motivo,
            "ChatDiscord":         chat_discord.isoformat(),
            "chat_discord":        chat_discord.isoformat(),
            "chat_discord_data":   _formatar_data_br(chat_discord),
            "chat_discord_ms":     int(chat_discord.timestamp() * 1000),
            "conversa":            log_text,
        }
        await n8n_utils.send(config.N8N_WEBHOOK_TI, payload)

    # 4. Remove membros sem cargo TI
    allowed = {cargo_ti.id}
    removed, failed = await remove_members_except(channel, guild, allowed)

    summary = f"📁 Log salvo. Removidos: {len(removed)} usuário(s)."
    if failed:
        summary += f" Falhas: {len(failed)} (veja terminal)."

    if interaction:
        try:
            await interaction.followup.send(summary)
        except Exception:
            try:
                await channel.send(summary)
            except Exception:
                pass
    else:
        try:
            await channel.send(summary)
        except Exception:
            pass

    print(f"[TI] Removidos: {removed} | Falhas: {failed}")

    try:
        await channel.delete()
        print(f"[TI] Tópico '{channel.name}' deletado.")
    except Exception as e:
        print(f"[TI] Erro ao deletar tópico: {e}")


# ── Auto-fechamento por inatividade (chamado pelo main.py) ────────────────────

async def auto_fechar_ti(thread: discord.Thread, guild: discord.Guild) -> None:
    """
    Tópico de TI inativo por 8h.
    Fecha automaticamente: gera logs, envia N8N e deleta a thread.
    """
    cargo_ti = _get_cargo_ti(guild, guild.id)
    if not cargo_ti:
        print(f"[TI-AUTO] Cargo TI não encontrado para guild {guild.id}")
        return

    cfg = _ti_cfg(guild.id)

    # Remove membros sem cargo TI
    allowed = {cargo_ti.id}
    await remove_members_except(thread, guild, allowed)

    await thread.send(
        "⏰ Este chamado atingiu **8 horas de inatividade**.\n"
        "Fechamento automático em andamento..."
    )

    form_response = {
        "confirmado_por": "Auto-fechamento (inatividade)",
        "auto_close": True,
    }

    try:
        await _process_and_finalize(
            interaction=None,
            guild=guild,
            channel=thread,
            cfg=cfg,
            cargo_ti=cargo_ti,
            form_response=form_response,
        )
        print(f"[TI-AUTO] Tópico '{thread.name}' processado automaticamente.")
    except Exception as e:
        print(f"[TI-AUTO] Erro ao processar automaticamente: {e}")


# ── Registro do comando !logs ─────────────────────────────────────────────────

def setup(bot: commands.Bot) -> None:

    @bot.command(name="logs")
    async def gerar_logs(ctx: commands.Context):
        guild = ctx.guild
        channel = ctx.channel

        if not guild or not channel:
            await ctx.reply("⚠️ Não foi possível identificar servidor/canal.", mention_author=False)
            return

        if guild.id not in config.SERVIDORES:
            return

        if not isinstance(channel, discord.Thread):
            await ctx.reply(
                "❌ `!logs` só pode ser usado dentro de um tópico.", mention_author=False
            )
            return

        cargo_ti = _get_cargo_ti(guild, guild.id)
        if not cargo_ti:
            await ctx.reply("⚠️ Cargo de T.I. não encontrado.", mention_author=False)
            return

        if cargo_ti not in ctx.author.roles:
            return

        if channel.id in _LOGS_ACTIVE_THREADS:
            return
        _LOGS_ACTIVE_THREADS.add(channel.id)

        # 1. Remove imediatamente todos sem cargo TI
        allowed = {cargo_ti.id}
        removed, failed = await remove_members_except(channel, guild, allowed)

        summary_remove = f"🧹 Removidos: {len(removed)} usuário(s)."
        if failed:
            summary_remove += f" Falhas: {len(failed)}."
        try:
            await ctx.reply(summary_remove, mention_author=False)
        except Exception:
            pass

        # 2. Envia formulário
        view = LogsFormView(guild_id=guild.id, timeout=120.0)
        try:
            await ctx.reply(
                "Por favor, selecione **Empresa** e **Nível** e clique em **Confirmar**.",
                view=view,
                mention_author=False,
            )
        except Exception as e:
            print(f"[TI] Erro ao enviar LogsFormView: {e}")
            await ctx.reply("❌ Não foi possível abrir o formulário.", mention_author=False)
            return

        await view.wait()
        _LOGS_ACTIVE_THREADS.discard(channel.id)
        # Marca que aquela thread já está processando !logs, para evitar duplicar mensagens/formulários se alguém mandar o comando duas vezes.

        if not view.form_response:
            try:
                await ctx.reply(
                    "❌ Formulário não preenchido dentro do tempo. Operação abortada.",
                    mention_author=False,
                )
            except Exception:
                pass
