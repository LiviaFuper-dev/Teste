"""
_google_drive.py — Suporte a problemas de Google Drive.

Wrapper fino sobre _email_base.py. Mantém a função _criar_thread_google_drive()
que é importada pelo __init__.py.
"""

import discord

from ._email_base import criar_thread_suporte_email


async def _criar_thread_google_drive(interaction: discord.Interaction) -> None:
    await criar_thread_suporte_email(
        interaction,
        system_name="Google Drive",
        emoji="📁",
        log_prefix="GDRIVE",
    )
