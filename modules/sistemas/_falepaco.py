"""
_falepaco.py — Sistema Falepaco.

Fluxo:
  Usuário clica "Falepaco" → ephemeral FalepacoView (some após escolha)

  → "Baixar Falepaco"   → thread "3 - Falepaco - {usuario}" + pinga cargo TI
                           payload inicializado → enviado via !sistema

  → "Dúvidas com senha" → ephemeral orientando a chamar o gestor (some em 15s)
                           payload enviado imediatamente ao N8N (sem thread)

  → "Outros"            → thread "3 - Falepaco - {usuario}" + pinga cargo TI
                           payload inicializado → enviado via !sistema
"""

import asyncio
import datetime

import discord

import config
from ._engine import PENDING_PAYLOADS, _ping_role, _update_step
from utils import n8n as n8n_utils

_CARGO_FALEPACO_ID = 1415390806541598831


class FalepacoView(discord.ui.View):
    """Ephemeral com as três opções do Falepaco."""

    def __init__(self, menu_interaction: discord.Interaction = None):
        super().__init__(timeout=None)
        self.menu_interaction = menu_interaction

    async def _fechar_este_ephemeral(self) -> None:
        """Apaga o FalepacoView ephemeral após 3s."""
        if self.menu_interaction:
            await asyncio.sleep(3)
            try:
                await self.menu_interaction.delete_original_response()
            except Exception:
                pass

    async def _criar_thread(
        self, interaction: discord.Interaction, opcao: str
    ) -> discord.Thread | None:
        """Cria a thread, inicializa o payload e retorna a thread."""
        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if not guild or not channel:
            await interaction.followup.send("Erro ao identificar servidor/canal.", ephemeral=True)
            return None

        try:
            thread = await channel.create_thread(
                name=f"3 - Falepaco - {user.display_name}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=config.THREAD_AUTO_ARCHIVE_MINUTES,
            )
        except Exception as e:
            await interaction.followup.send("Erro ao criar o tópico.", ephemeral=True)
            print(f"[FALEPACO] Erro ao criar thread ({opcao}): {e}")
            return None

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
            "system": "Falepaco",
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
            "steps": {
                "opcao": opcao,
            },
        }
        print(f"[FALEPACO] Payload inicializado ({opcao}): thread {thread.id}")
        return thread

    # ── Baixar Falepaco ───────────────────────────────────────────────────────

    @discord.ui.button(label="Baixar Falepaco 📥", style=discord.ButtonStyle.primary)
    async def baixar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        thread = await self._criar_thread(interaction, "download")
        if not thread:
            return

        await _ping_role(
            thread, interaction.guild, _CARGO_FALEPACO_ID,
            f"👋 Olá {interaction.user.mention}, tudo bem?\n\n"
            "Vi que você deseja baixar o **Falepaco** 📥 e nossa equipe vai te ajudar com isso!\n\n"
            "Antes de começarmos, preciso que você confirme uma coisa:\n"
            "💻 **Você possui o aplicativo AnyDesk instalado no seu computador?**\n"
            "🔎 Para verificar:\n"
            "1️⃣ Aperte a **tecla Windows** no seu teclado.\n"
            "2️⃣ Na barra de pesquisa, digite **AnyDesk**.\n"
            "3️⃣ Abra o aplicativo.\n\n"
            "Assim que ele abrir, você verá um **número ao lado da mensagem \"Este dispositivo\"**.\n"
            "📨 **Envie esse número aqui no chat para nós.**\n\n"
            "❗ Caso você **não tenha o AnyDesk instalado**, pode baixar por aqui:\n"
            "🔗 https://anydesk.com/pt/downloads/windows\n\n"
            "Depois é só clicar em **\"Baixe Agora\"** ⬇️\n\n"
            "👨‍💻 Enquanto isso, já estou chamando nossa equipe para te ajudar no restante do processo.\n"
            "Obrigado! 🙌",
        )

        await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)
        await self._fechar_este_ephemeral()

    # ── Dúvidas com senha ─────────────────────────────────────────────────────

    @discord.ui.button(label="Dúvidas com senha 🔑", style=discord.ButtonStyle.secondary)
    async def esqueci_senha(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Oi, tudo bem? 😊 Que pena que você está com dúvidas sobre sua senha!\n\n"
            "🔑 Para ter acesso ou recuperar a senha do **Falepaco** você precisa falar "
            "diretamente com o seu **gestor**, tá bem? Ele é o responsável por fazer esse ajuste pra você.\n\n"
            "📌 Qualquer outra dúvida, pode me chamar aqui que eu apareço! 🚀",
            ephemeral=True,
        )

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        if guild and config.N8N_WEBHOOK_SISTEMAS:
            payload = {
                "event": "falepaco_duvida_senha",
                "system": "Falepaco",
                "opcao": "duvida_senha",
                "user_id": user.id,
                "user_name": user.display_name,
                "user_tag": str(user),
                "guild_id": guild.id,
                "guild_name": guild.name,
                "channel_id": getattr(channel, "id", None),
                "channel_name": getattr(channel, "name", None),
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            }
            await n8n_utils.send(config.N8N_WEBHOOK_SISTEMAS, payload)
            print(f"[FALEPACO] Payload 'duvida_senha' enviado para N8N.")

        await self._fechar_este_ephemeral()

        await asyncio.sleep(15)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    # ── Outros ────────────────────────────────────────────────────────────────

    @discord.ui.button(label="Outros ❓", style=discord.ButtonStyle.danger)
    async def outros(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        thread = await self._criar_thread(interaction, "outros")
        if not thread:
            return

        await _ping_role(
            thread, interaction.guild, _CARGO_FALEPACO_ID,
            f"Olá, {interaction.user.mention}! Tudo bem? 😊\n\n"
            "Recebemos seu chamado sobre o **Falepaco**.\n\n"
            "Por favor, descreva aqui o que está acontecendo com o máximo de detalhes possível "
            "e nossa equipe entrará em contato em breve. 🙌",
        )

        await interaction.followup.send("Tópico criado! Acesse-o para continuar.", ephemeral=True)
        await self._fechar_este_ephemeral()


async def _abrir_falepaco_menu(interaction: discord.Interaction) -> None:
    """Envia o ephemeral com FalepacoView."""
    await interaction.response.send_message(
        "Selecione uma opção do **Falepaco**:",
        view=FalepacoView(menu_interaction=interaction),
        ephemeral=True,
    )