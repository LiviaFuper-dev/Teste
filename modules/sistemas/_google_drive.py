"""
_google_drive.py — Suporte a problemas de Google Drive.

Fluxo:
  1. Usuário clica "Google Drive" → thread criada
  2. Bot pergunta o domínio: @gmail.com ou @mlradvogados.com
  3. Usuário clica em um dos botões → modal abre com:
     - e-mail completo
     - descrição do problema
  4. Bot verifica o e-mail: se contiver "mlradvogados" pinga cargo MLR,
     caso contrário pinga cargo Gmail
  5. Bot envia resumo no tópico para o atendente

Usa os mesmos cargos do módulo de E-mail:
  config.EMAIL_MLR_ROLE_ID   → @mlradvogados.com
  config.EMAIL_GMAIL_ROLE_ID → @gmail.com
"""

import datetime

import discord

import config
from ._engine import PENDING_PAYLOADS, _disable_view, _ping_role, _update_step


def _detectar_role_id(email: str) -> int:
    email_lower = email.lower().replace(" ", "").replace(".", "").replace("-", "")
    if "mlradvogados" in email_lower or "mlr" in email_lower.split("@")[-1]:
        return config.EMAIL_MLR_ROLE_ID
    return config.EMAIL_GMAIL_ROLE_ID


def _label_dominio(role_id: int) -> str:
    if role_id == config.EMAIL_MLR_ROLE_ID:
        return "@mlradvogados.com"
    return "@gmail.com"


# ── Modal ─────────────────────────────────────────────────────────────────────

class GoogleDriveInfoModal(discord.ui.Modal, title="Suporte — Google Drive"):

    email = discord.ui.TextInput(
        label="Qual é o seu e-mail completo?",
        placeholder="ex: joao@gmail.com  ou  joao.fuper@gmail.com",
        required=True,
        max_length=200,
    )

    problema = discord.ui.TextInput(
        label="Qual o problema que você está enfrentando?",
        style=discord.TextStyle.paragraph,
        placeholder="Descreva o problema com o máximo de detalhes possível...",
        required=True,
        max_length=1000,
    )

    def __init__(self, botao_clicado: str):
        super().__init__()
        self.botao_clicado = botao_clicado

    async def on_submit(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        guild = interaction.guild
        user = interaction.user

        email_value = self.email.value.strip()
        problema_value = self.problema.value.strip()

        role_id = _detectar_role_id(email_value)
        dominio_label = _label_dominio(role_id)

        _update_step(thread.id, "botao_clicado", self.botao_clicado)
        _update_step(thread.id, "email_usuario", email_value)
        _update_step(thread.id, "dominio_detectado", dominio_label)
        _update_step(thread.id, "problema", problema_value)

        await interaction.response.defer()

        await _ping_role(
            thread,
            guild,
            role_id,
            (
                f"📁 **Novo chamado de Google Drive**\n\n"
                f"👤 **Usuário:** {user.mention}\n"
                f"📬 **E-mail informado:** `{email_value}`\n"
                f"🏷️ **Encaminhado para:** {dominio_label}\n\n"
                f"📝 **Problema relatado:**\n> {problema_value}"
            ),
        )


# ── View ──────────────────────────────────────────────────────────────────────

class GoogleDriveDomainView(discord.ui.View):

    def __init__(self, original_user_id: int):
        super().__init__(timeout=None)
        self.original_user_id = original_user_id

    async def _check_user(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode interagir aqui.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="@gmail.com", style=discord.ButtonStyle.danger)
    async def gmail(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.send_modal(GoogleDriveInfoModal(botao_clicado="gmail"))

    @discord.ui.button(label="@mlradvogados.com", style=discord.ButtonStyle.primary)
    async def mlr(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        await interaction.response.send_modal(GoogleDriveInfoModal(botao_clicado="mlradvogados"))


# ── Criação da thread ─────────────────────────────────────────────────────────

async def _criar_thread_google_drive(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if not guild or not channel:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    try:
        thread = await channel.create_thread(
            name=f"3 - Google Drive - {user.display_name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(f"[GDRIVE] Erro ao criar thread: {e}")
        return

    try:
        await thread.join()
    except Exception:
        try:
            await thread.add_user(interaction.client.user)
        except Exception:
            pass

    try:
        await thread.add_user(user)
    except Exception:
        pass

    PENDING_PAYLOADS[thread.id] = {
        "event":        "topic_created",
        "system":       "Google Drive",
        "user_id":      user.id,
        "user_name":    user.display_name,
        "user_tag":     str(user),
        "guild_id":     guild.id,
        "guild_name":   guild.name,
        "channel_id":   channel.id,
        "channel_name": getattr(channel, "name", None),
        "thread_id":    thread.id,
        "thread_name":  thread.name,
        "thread_url":   getattr(thread, "jump_url", None),
        "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
        "steps": {},
    }
    print(f"[GDRIVE] Payload inicializado: thread {thread.id}")

    try:
        await thread.send(
            f"Olá, {user.mention}! Tudo bem? 😊\n\n"
            "Para conseguirmos te ajudar, preciso saber qual é o seu e-mail. "
            "Ele termina com **@gmail.com** ou **@mlradvogados.com**?",
            view=GoogleDriveDomainView(user.id),
        )
    except Exception as e:
        print(f"[GDRIVE] Erro ao enviar mensagem na thread: {e}")

    await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)