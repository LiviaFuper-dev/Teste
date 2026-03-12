"""
utils/thread_utils.py — Funções reutilizáveis de gerenciamento de threads.

Todos os módulos usam estas funções para:
  - Entrar na thread como bot (safe_join_thread)
  - Remover membros sem cargos permitidos (remove_members_except)
  - Desabilitar views após interação (disable_view)
"""

import asyncio
import discord


async def safe_join_thread(bot_user: discord.ClientUser, thread: discord.Thread) -> bool:
    """
    Garante que o bot está na thread como membro.
    Tenta thread.join() primeiro; fallback para add_user() se falhar.
    """
    try:
        await thread.join()
        await asyncio.sleep(0.15)
        return True
    except Exception as e:
        print(f"[WARN] thread.join() falhou ({e}), tentando add_user()...")
        try:
            await thread.add_user(bot_user)
            await asyncio.sleep(0.15)
            return True
        except Exception as e2:
            print(f"[WARN] add_user() também falhou: {e2}")
            return False


async def remove_members_except(
    thread: discord.Thread,
    guild: discord.Guild,
    allowed_role_ids: set[int],
) -> tuple[list[str], list[str]]:
    """
    Remove da thread todos os membros que NÃO possuem nenhum dos cargos
    em `allowed_role_ids`. Nunca remove bots.

    Retorna (lista_removidos, lista_falhas) — ambas com display_names.
    """
    removed: list[str] = []
    failed: list[str] = []

    await asyncio.sleep(0.15)  # aguarda estabilização da lista de membros

    for tm in list(thread.members):
        try:
            member = guild.get_member(tm.id) or await guild.fetch_member(tm.id)
        except Exception:
            continue

        if not member or member.bot:
            continue

        member_role_ids = {r.id for r in member.roles}
        if member_role_ids & allowed_role_ids:
            continue  # tem pelo menos um cargo permitido → mantém

        try:
            await thread.remove_user(member)
            removed.append(member.display_name)
            print(f"[THREAD] Removido: {member.display_name} (thread: {thread.name})")
        except Exception as e:
            failed.append(member.display_name)
            print(f"[THREAD ERROR] Falha ao remover {member.display_name}: {e}")

    return removed, failed


async def disable_view(interaction: discord.Interaction, view: discord.ui.View) -> None:
    """
    Desabilita todos os componentes de uma view e edita a mensagem original.
    Chame isso quando um fluxo de botões avança para o próximo passo.
    """
    for item in view.children:
        try:
            item.disabled = True
        except Exception:
            pass
    try:
        if getattr(interaction, "message", None):
            await interaction.message.edit(view=view)
    except Exception as e:
        print(f"[WARN] disable_view: {e}")