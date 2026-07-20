"""
_chatguru.py - Passo final do fluxo ChatGuru.

Contém:
  - ChatGuruFourthView  (existe mensagem de erro?)
  - ErrorNumberModal    (coleta o código e consulta solutions.json)
"""

import json
import os
import re

import discord

from ._engine import _update_step, _disable_view, _ping_role, role_id_for_system

# solutions.json
# __file__ -> modules/sistemas/_chatguru.py
# dirname x1 -> modules/sistemas/
# dirname x2 -> modules/
# dirname x3 -> raiz do projeto (onde está solutions.json)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SOLUTIONS_FILE = os.path.join(_BASE_DIR, "solutions.json")

try:
    with open(_SOLUTIONS_FILE, "r", encoding="utf-8") as _f:
        ERROR_DB: dict[str, str] = {str(k): v for k, v in json.load(_f).items()}
    print(f"[SISTEMAS] solutions.json carregado - {len(ERROR_DB)} entradas.")
except FileNotFoundError:
    ERROR_DB = {}
    print("[SISTEMAS] solutions.json não encontrado. ERROR_DB vazio.")
except Exception as _e:
    ERROR_DB = {}
    print(f"[SISTEMAS] Erro ao carregar solutions.json: {_e}")


class ChatGuruFourthView(discord.ui.View):
    """Pergunta se existe mensagem de erro visível na tela."""

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

    @discord.ui.button(label="Não existe", style=discord.ButtonStyle.danger)
    async def no_exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "mensagem_erro_existe", "nao")
        await _disable_view(interaction, self)
        await interaction.response.defer()
        await _ping_role(
            interaction.channel,
            interaction.guild,
            role_id_for_system(interaction.guild.id, "ChatGuru"),
            "O usuário informou que não há nenhuma mensagem de erro visível. Chamando a equipe do ChatGuru:",
        )

    @discord.ui.button(label="existe", style=discord.ButtonStyle.primary)
    async def exist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_user(interaction):
            return
        _update_step(interaction.channel.id, "mensagem_erro_existe", "sim")
        await _disable_view(interaction, self)
        await interaction.response.send_modal(ErrorNumberModal(self.original_user_id))


class ErrorNumberModal(discord.ui.Modal, title="Número do erro"):
    """Coleta o código de erro e tenta resolver automaticamente via solutions.json."""

    error_number = discord.ui.TextInput(
        label="Informe o número do erro",
        placeholder="Ex: 131049 - mensagem...",
        style=discord.TextStyle.short,
        required=True,
        max_length=200,
    )

    def __init__(self, original_user_id: int):
        super().__init__()
        self.original_user_id = original_user_id

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.original_user_id:
            await interaction.response.send_message(
                "Apenas o solicitante pode enviar este dado.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        thread = interaction.channel
        guild = interaction.guild
        user_input = self.error_number.value.strip()

        m = re.match(r"^\s*(\d{1,6})", user_input) or re.search(r"(\d{1,6})", user_input)
        code = m.group(1) if m else None

        _update_step(thread.id, "codigo_erro_informado", user_input)

        try:
            await thread.send(f"Número/erro informado pelo usuário:\n{user_input}")
        except Exception:
            pass

        solution = (ERROR_DB.get(str(code)) if code else None) or ERROR_DB.get(user_input)

        if solution:
            dm_enviado = False
            try:
                await interaction.user.send(
                    f"Solução encontrada para o código `{code}` "
                    f"(chamado: **{getattr(thread, 'name', 'sem nome')}**):\n\n"
                    f"{solution}\n\n"
                    "Caso o problema persista, entre em contato com a equipe de suporte."
                )
                dm_enviado = True
            except discord.Forbidden:
                print(f"[SISTEMAS] DM fechada para {interaction.user}")
            except Exception as e:
                print(f"[SISTEMAS] Erro ao enviar DM: {e}")

            aviso = "Solução encontrada e enviada no privado do usuário.\n"
            if not dm_enviado:
                aviso += "Não foi possível enviar DM (DMs fechadas).\n"
            try:
                await thread.send(aviso)
            except Exception:
                pass

            try:
                await thread.remove_user(interaction.user)
            except Exception:
                pass

            await _ping_role(
                thread,
                guild,
                role_id_for_system(interaction.guild.id, "ChatGuru"),
                "Solução automática aplicada. Equipe ChatGuru, fiquem cientes do chamado:",
            )

            _update_step(thread.id, "solution_found", "sim")
            _update_step(thread.id, "solution_text", solution)
            _update_step(thread.id, "dm_enviado", "sim" if dm_enviado else "nao")
            return

        await _ping_role(
            thread,
            guild,
            role_id_for_system(interaction.guild.id, "ChatGuru"),
            "Não encontrei uma solução automática para esse código. Chamando equipe ChatGuru.",
        )
        await interaction.followup.send(
            "Obrigado, a equipe de T.I foi notificada. (Nenhuma solução automática encontrada.)",
            ephemeral=True,
        )
