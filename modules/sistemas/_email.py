"""
_email.py — Suporte a problemas de e-mail.

Wrapper fino sobre _email_base.py. Mantém a função _criar_thread_email()
que é importada pelo __init__.py.
"""

import discord

from ._email_base import criar_thread_suporte_email


async def _criar_thread_email(interaction: discord.Interaction) -> None:
    await criar_thread_suporte_email(
        interaction,
        system_name="E-mail",
        emoji="📧",
        log_prefix="EMAIL",
    )
