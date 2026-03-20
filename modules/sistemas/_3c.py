"""
_3cplus.py — Sistema 3c+.

Fluxo:
  Usuário clica "3c+" no ServicesView → thread "3 - 3c+ - {usuario}"
  → pinga o cargo responsável
  → TI digita !sistema → payload enviado ao N8N
"""

import datetime

import discord

import config
from ._engine import PENDING_PAYLOADS, _ping_role

_CARGO_3CPLUS_ID = 1484627219518062864


async def _abrir_3cplus(interaction: discord.Interaction) -> None:
    """Cria a thread do 3c+, inicializa o payload e pinga o cargo."""
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    if not guild or not channel:
        await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
        return

    try:
        thread = await channel.create_thread(
            name=f"3 - 3c+ - {user.display_name}",
            type=discord.ChannelType.private_thread,
            auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
        )
    except Exception as e:
        await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
        print(f"[3CPLUS] Erro ao criar thread: {e}")
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
        "event": "topic_created",
        "system": "3c+",
        "user_id": user.id,
        "user_name": user.display_name,
        "user_tag": str(user),
        "guild_id": guild.id,
        "guild_name": guild.name,
        "channel_id": channel.id,
        "channel_name": getattr(channel, "name", None),
        "thread_id": thread.id,
        "thread_name": thread.name,
        "thread_url": getattr(thread, "jump_url", None),
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "steps": {},
    }
    print(f"[3CPLUS] Payload inicializado: thread {thread.id}")

    await _ping_role(
        thread, guild, _CARGO_3CPLUS_ID,
        f"Olá, {user.mention}! Tudo bem? 😊\n\n"
        "Recebemos seu chamado sobre o **3c+**.\n"
        "Por favor, descreva aqui o que está acontecendo e nossa equipe entrará em contato em breve.",
    )

    await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)