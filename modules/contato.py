"""
modules/contato.py — Módulo de Recuperação de Contato.

Fluxo:
  - Usuário clica em "Recuperar Telefone 📞" no menu → Modal abre direto (sem ephemeral)
  - Preenche Nome e CPF → payload enviado ao N8N
    • Se N8N retorna { "existe": true } → thread NÃO é criada, DM enviada ao usuário
    • Caso contrário → thread privada criada com TARGET_USER_ID adicionado
  - Task de inatividade: a cada 60s verifica threads sem atividade e envia aviso
  - !contato: remove TARGET_USER_ID, envia mensagem de conclusão, agenda delete

Numeração: 2 — todos os tópicos abrem como "3 - NomeDoUsuário"
"""

import asyncio
import datetime
import json
import re
from pathlib import Path

import discord
from discord.ext import commands, tasks

import config
from utils.command_help import reply_commands_help
from utils.thread_utils import safe_join_thread

# ── Regex de validação de CPF ──────────────────────────────────────────────────
_CPF_REGEX = re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$")

# ── Controle de inatividade ────────────────────────────────────────────────────
# { thread_id: {"last_activity": datetime} }
_THREAD_ACTIVITY: dict[int, dict] = {}
_FINALIZADOS_FILE = Path("data/contatos_finalizados.json")
_FINALIZADOS_FILE.parent.mkdir(parents=True, exist_ok=True)

# O Brasil não utiliza horário de verão atualmente. Usar o fuso fixo evita
# depender do pacote tzdata nas instalações do bot no Windows.
_SAO_PAULO_TIMEZONE = datetime.timezone(datetime.timedelta(hours=-3))

# ── Referência ao bot (preenchida em setup) ───────────────────────────────────
_bot: commands.Bot | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _contato_cfg(guild_id: int | None) -> dict:
    if not guild_id:
        return {}
    return config.SERVIDORES.get(guild_id, {}).get("contato", {})


def _target_role_id(guild_id: int | None) -> int:
    return int(_contato_cfg(guild_id).get("target_role_id") or config.CONTATO_TARGET_ROLE_ID)


def _load_finalizados() -> set[int]:
    if not _FINALIZADOS_FILE.exists():
        return set()
    try:
        return {int(x) for x in json.loads(_FINALIZADOS_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return set()


def _save_finalizados(ids: set[int]) -> None:
    try:
        _FINALIZADOS_FILE.write_text(
            json.dumps(sorted(ids), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[CONTATO] Erro ao salvar finalizados: {e}")


def mark_thread_finalizada(thread_id: int) -> None:
    ids = _load_finalizados()
    ids.add(int(thread_id))
    _save_finalizados(ids)


def is_thread_finalizada(thread_id: int) -> bool:
    return int(thread_id) in _load_finalizados()


def _validar_cpf(cpf: str) -> bool:
    return bool(_CPF_REGEX.match(cpf))


def _as_utc(moment: datetime.datetime) -> datetime.datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)


def _next_business_day_start(moment: datetime.datetime) -> datetime.datetime:
    next_day = moment.date() + datetime.timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += datetime.timedelta(days=1)
    return datetime.datetime.combine(
        next_day,
        datetime.time(config.CONTATO_BUSINESS_START_HOUR),
        tzinfo=_SAO_PAULO_TIMEZONE,
    )


def _normalize_to_business_time(moment: datetime.datetime) -> datetime.datetime:
    """Move um instante local para o primeiro horário comercial válido."""
    start = moment.replace(
        hour=config.CONTATO_BUSINESS_START_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = moment.replace(
        hour=config.CONTATO_BUSINESS_END_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )

    if moment.weekday() >= 5:
        while start.weekday() >= 5:
            start += datetime.timedelta(days=1)
        return start
    if moment < start:
        return start
    if moment >= end:
        return _next_business_day_start(moment)
    return moment


def _business_deadline(last_activity: datetime.datetime) -> datetime.datetime:
    """Calcula o prazo somando somente horas comerciais de segunda a sexta."""
    current = _normalize_to_business_time(
        _as_utc(last_activity).astimezone(_SAO_PAULO_TIMEZONE)
    )
    remaining = datetime.timedelta(hours=config.CONTATO_INACTIVITY_BUSINESS_HOURS)

    while remaining:
        end = current.replace(
            hour=config.CONTATO_BUSINESS_END_HOUR,
            minute=0,
            second=0,
            microsecond=0,
        )
        available = end - current
        if remaining <= available:
            return current + remaining
        remaining -= available
        current = _next_business_day_start(current)

    return current


def _is_business_time(moment: datetime.datetime) -> bool:
    local = moment.astimezone(_SAO_PAULO_TIMEZONE)
    return (
        local.weekday() < 5
        and config.CONTATO_BUSINESS_START_HOUR
        <= local.hour
        < config.CONTATO_BUSINESS_END_HOUR
    )


def register_thread_activity(thread_id: int) -> None:
    _THREAD_ACTIVITY[thread_id] = {
        "last_activity": datetime.datetime.now(datetime.timezone.utc)
    }


def get_thread_activity(thread_id: int) -> datetime.datetime | None:
    raw = _THREAD_ACTIVITY.get(thread_id, {}).get("last_activity")
    if raw is None:
        return None
    if raw.tzinfo is None:
        return raw.replace(tzinfo=datetime.timezone.utc)
    return raw.astimezone(datetime.timezone.utc)


# ── Task de inatividade ───────────────────────────────────────────────────────

@tasks.loop(seconds=60)
async def _verificar_inatividade():
    agora = datetime.datetime.now(datetime.timezone.utc)
    para_remover = []

    # A mensagem nunca deve ser enviada fora do horário comercial.
    if not _is_business_time(agora):
        return

    for thread_id, info in list(_THREAD_ACTIVITY.items()):
        prazo = _business_deadline(info["last_activity"])
        if agora.astimezone(_SAO_PAULO_TIMEZONE) < prazo:
            continue

        thread = None
        for guild_id in config.SERVIDORES:
            guild = _bot.get_guild(guild_id)
            if guild:
                thread = guild.get_thread(thread_id)
                if thread:
                    break

        if thread is None:
            para_remover.append(thread_id)
            continue

        try:
            await thread.send(config.CONTATO_INACTIVITY_MESSAGE)
            _THREAD_ACTIVITY[thread_id]["last_activity"] = datetime.datetime.now(
                datetime.timezone.utc
            )
            print(f"[CONTATO] Mensagem de inatividade enviada na thread {thread_id}")
        except Exception as e:
            print(f"[CONTATO] Erro ao enviar inatividade na thread {thread_id}: {e}")

    for tid in para_remover:
        _THREAD_ACTIVITY.pop(tid, None)


# ── Fechar tópico após delay ──────────────────────────────────────────────────

async def _fechar_topico_apos_delay(thread: discord.Thread, guild_id: int):
    await asyncio.sleep(config.CONTATO_CLOSE_DELAY_SECONDS)
    try:
        guild = _bot.get_guild(guild_id)
        if guild and guild.get_thread(thread.id) is None:
            print(f"[CONTATO] Tópico {thread.id} já deletado.")
            return

        canal_logs_id = config.SERVIDORES.get(guild_id, {}).get("canal_logs")
        if guild and canal_logs_id:
            try:
                from utils.logs import enviar_log_conversa
                await enviar_log_conversa(
                    thread,
                    guild,
                    canal_logs_id,
                    prefixo_log="[CONTATO]",
                    header_extra="=== Contato concluido ===\nMotivo: Fechado apos !contato",
                )
            except Exception as e:
                print(f"[CONTATO] Erro ao enviar log de fechamento: {e}")

        await thread.delete()
        print(f"[CONTATO] Tópico {thread.id} fechado automaticamente.")
    except discord.NotFound:
        print(f"[CONTATO] Tópico {thread.id} não encontrado (já deletado).")
    except Exception as e:
        print(f"[CONTATO] Erro ao fechar tópico {thread.id}: {e}")


# ── Modal principal ───────────────────────────────────────────────────────────

class ContatoModal(discord.ui.Modal):
    """Aberto diretamente pelo botão do menu. Sem ephemeral intermediário."""

    def __init__(self):
        super().__init__(title="Recuperar Contato")

        self.nome = discord.ui.TextInput(
            label="Nome do contato",
            placeholder="Nome completo",
            required=True,
            max_length=200,
        )
        self.cpf = discord.ui.TextInput(
            label="CPF do contato",
            placeholder="000.000.000-00",
            required=True,
            max_length=20,
        )
        self.add_item(self.nome)
        self.add_item(self.cpf)

    async def on_submit(self, interaction: discord.Interaction):
        cpf = self.cpf.value.strip()
        if not _validar_cpf(cpf):
            await interaction.response.send_message(
                "❌ **CPF inválido!**\n\nUse o formato correto: `000.000.000-00`",
                ephemeral=True,
            )
            return
        await _criar_thread_contato(
            interaction,
            self.nome.value.strip(),
            cpf,
        )


# ── Criação da thread de contato ──────────────────────────────────────────────

async def _criar_thread_contato(
    interaction: discord.Interaction,
    nome_contato: str,
    cpf_contato: str,
) -> None:
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    channel = interaction.channel

    if not guild or not channel:
        await interaction.followup.send(
            "⚠️ Não foi possível identificar servidor/canal.", ephemeral=True
        )
        return

    n8n_url = config.N8N_WEBHOOK_CONTATO

    # ── 1. Envia payload ao N8N ────────────────────────────────────────────────
    payload = {
        "created_at":   datetime.datetime.utcnow().isoformat() + "Z",
        "guild_id":     guild.id,
        "channel_id":   getattr(channel, "id", None),
        "author_id":    interaction.user.id,
        "author_name":  interaction.user.display_name,
        "contact_name": nome_contato,
        "contact_cpf":  cpf_contato,
    }

    resp = {"ok": False, "json": {}}
    if n8n_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    n8n_url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r:
                    status = r.status
                    try:
                        body = await r.json()
                    except Exception:
                        body = {}
                    resp = {"ok": status == 200, "json": body}
                    print(f"[CONTATO] N8N status: {status} | body: {body}")
        except Exception as e:
            print(f"[CONTATO] Erro ao chamar N8N: {e}")

    # ── 2. Verifica flag "existe" ──────────────────────────────────────────────
    existe = resp.get("ok") and resp.get("json", {}).get("existe", False)

    if existe:
        try:
            await interaction.user.send(
                "Opa! Tudo bem? :octagonal_sign:\n\n"
                "O sistema identificou que a sua última solicitação no puxador é de um contato que "
                "**já foi recuperado antes.**\n\n"
                "Como a plataforma encerra chamados duplicados automaticamente para não travar a fila "
                "de atendimento de todo mundo, essa sua solicitação foi fechada, beleza?\n\n"
                "Mas fica tranquilo que o seu contato tá na mão. Você pode acessar e encontrar os "
                "números dele direto por este link: :point_down:\n\n"
                "https://app.clickup.com/9011605202/v/li/901112971241\n\n"
                "**Dica: Para economizar o seu próprio tempo, dê sempre uma conferida rápida, "
                "pesquisando na lupa, se o contato já não está na base antes de puxar!** :rocket:"
            )
        except discord.Forbidden:
            print(f"[CONTATO] Não foi possível enviar DM para {interaction.user} (DMs fechadas)")

        await interaction.followup.send(
            "⚠️ Contato já existe no sistema. Verifique sua DM.", ephemeral=True
        )
        return

    # ── 3. Cria a thread ───────────────────────────────────────────────────────
    thread_name = f"3 - {interaction.user.display_name}"
    try:
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Sem permissão para criar tópico privado.", ephemeral=True
        )
        return
    except Exception as e:
        print(f"[CONTATO] Erro ao criar thread: {e}")
        await interaction.followup.send("❌ Erro ao criar tópico.", ephemeral=True)
        return

    # Bot entra na thread
    await safe_join_thread(interaction.client.user, thread)

    # Adiciona o solicitante
    try:
        await thread.add_user(interaction.user)
    except Exception:
        pass

    # Adiciona CONTATO_TARGET_ROLE_ID
    # feat: [FIX] 15/06 - menciona o cargo de contato na thread, sem adicionar usuário fixo
    role_id = _target_role_id(guild.id)
    if role_id:
        try:
            await thread.send(
                f"Novo chamado de recuperação de contato! <@&{role_id}>",
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
        except Exception as e:
            print(f"[CONTATO] Não foi possível mencionar CONTATO_TARGET_ROLE_ID: {e}")

    # Registra no controle de inatividade
    register_thread_activity(thread.id)

    # Re-envia payload com thread_id para o N8N
    payload["thread_id"]         = thread.id
    if n8n_url:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(
                    n8n_url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=15)
                )
        except Exception as e:
            print(f"[CONTATO] Erro ao re-enviar payload com thread_id: {e}")

    # Mensagem na thread
    status_txt = (
        "✅ Solicitação registrada."
        if resp.get("ok")
        else "⚠️ Falha ao contatar sistema externo."
    )
    try:
        await thread.send(
            f"📇 **Contato registrado**\n\n"
            f"**Nome:** {nome_contato}\n"
            f"**CPF:** {cpf_contato}\n\n"
            f"{status_txt}"
        )
    except Exception as e:
        print(f"[CONTATO] Erro ao enviar mensagem na thread: {e}")

    try:
        await interaction.followup.send(
            f"✅ Chamado criado: {thread.mention}", ephemeral=True
        )
    except Exception:
        pass


# ── Evento on_message (atualiza inatividade) ──────────────────────────────────

async def on_message_contato(message: discord.Message):
    """Chamado pelo on_message do main.py para atualizar o timer de inatividade."""
    if message.channel.id in _THREAD_ACTIVITY:
        _THREAD_ACTIVITY[message.channel.id]["last_activity"] = datetime.datetime.now(
            datetime.timezone.utc
        )


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(bot: commands.Bot) -> None:
    """
    Registra o comando !contato no bot.
    Chame UMA vez em main.py antes de bot.run().
    NÃO inicia tasks aqui — chame start_tasks() dentro do on_ready().
    """
    global _bot
    _bot = bot

    @bot.command(name="contato")
    async def contato_cmd(ctx: commands.Context):
        """
        Finaliza tópico de contato.
        1. Remove TARGET_USER_ID do tópico.
        2. Envia mensagem de conclusão.
        3. Agenda fechamento automático.
        """
        channel = ctx.channel

        if channel.type not in (
            discord.ChannelType.private_thread,
            discord.ChannelType.public_thread,
            discord.ChannelType.news_thread,
        ):
            await reply_commands_help(
                ctx,
                "O comando `!contato` só pode ser usado dentro de um tópico de Recuperar Contato.",
            )
            return

        if not ctx.channel.name.startswith("3 -"):
            await reply_commands_help(
                ctx,
                "O comando `!contato` só pode ser usado em tópicos de Recuperar Contato.",
            )
            return

        # Remove do controle de inatividade
        _THREAD_ACTIVITY.pop(channel.id, None)
        mark_thread_finalizada(channel.id)



        # Envia mensagem de conclusão
        try:
            await channel.send(config.CONTATO_CONCLUSAO_MESSAGE)
        except Exception as e:
            print(f"[CONTATO] Erro ao enviar mensagem de conclusão: {e}")

        # Agenda fechamento
        print(f"[CONTATO] Tópico {channel.id} será fechado em {config.CONTATO_CLOSE_DELAY_SECONDS}s.")
        asyncio.create_task(_fechar_topico_apos_delay(channel, ctx.guild.id))


def start_tasks() -> None:
    """
    Inicia tasks que precisam do event loop.
    Chame DENTRO do on_ready() do main.py.
    """
    if not _verificar_inatividade.is_running():
        _verificar_inatividade.start()
        print("[CONTATO] Task de inatividade iniciada.")
